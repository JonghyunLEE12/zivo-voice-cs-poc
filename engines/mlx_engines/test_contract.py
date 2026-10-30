"""Contract and cancellation tests without loading GPU weights.
Run from project root: .venv/bin/python -m unittest discover -s engines/mlx_engines -p 'test_*.py' -v
"""
import asyncio
import time
import unittest
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from serve import Runtime, Synthesis, create_app, prepare


def fake_worker(pipe, engine, model_path):
    pipe.send(("ready", {"load_ms": 12.0, "peak_mem_bytes": 1234}))
    while True:
        data = pipe.recv()
        if data["text"] == "stall":
            time.sleep(30)
        pipe.send(("audio", b"\x01\x00" * 240))
        if data["text"] == "stall_after_chunk":
            time.sleep(30)
        pipe.send(("done", {"synth_ms": 1.25, "peak_mem_bytes": 2345}))


class FakeRequest:
    disconnected = False
    async def is_disconnected(self):
        return self.disconnected


class Contract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = create_app("kokoro", lazy=True, worker_target=fake_worker)
        self.runtime = self.app.state.runtime
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.runtime.stop()

    async def test_info_is_lazy_and_exact(self):
        info = (await self.client.get("/v1/info")).json()
        self.assertEqual(set(info), {"engine", "model_id", "locales", "supports_streaming", "native_sample_rate", "load_ms", "peak_mem_bytes"})
        self.assertEqual(info["load_ms"], 0)
        self.assertFalse(info["supports_streaming"])
        self.assertIsNone(self.runtime.process)

    async def test_nonstream_pcm_headers_and_warmup(self):
        result = await self.client.post("/v1/synthesize", json={"text": "Hello", "locale": "en"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["content-type"], "audio/L16; rate=24000; channels=1")
        self.assertEqual(float(result.headers["x-synth-ms"]), 1.25)
        self.assertEqual(result.content, b"\x01\x00" * 240)
        warm = await self.client.post("/v1/warmup")
        self.assertTrue(warm.json()["warmed_up"])
        self.assertEqual(warm.json()["peak_mem_bytes"], 2345)

    async def test_stream_has_no_final_timing_header(self):
        result = await self.client.post("/v1/synthesize", json={"text": "Hello", "locale": "en", "stream": True})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("x-synth-ms", result.headers)
        self.assertEqual(len(result.content), 480)

    async def test_invalid_requests(self):
        for patch in ({"locale": "yue"}, {"locale": "ko"}, {"text": " "}, {"seed": -1},
                      {"voice": {"preset": "jf_alpha"}}, {"voice": {}},
                      {"voice": {"ref_audio_path": "x"}}, {"unknown": 1},
                      {"voice": {"preset": "af_heart", "ref_text": "no"}}):
            response = await self.client.post("/v1/synthesize", json={"text": "Hello", "locale": "en", **patch})
            self.assertEqual(response.status_code, 422, (patch, response.text))
        self.assertIsNone(self.runtime.process)

    async def test_shared_reference_and_path_validation(self):
        cn = prepare("voxcpm2", Synthesis(text="你好", locale="zh-CN"))
        tw = prepare("voxcpm2", Synthesis(text="你好", locale="zh-TW"))
        self.assertEqual(cn["voice"], tw["voice"])
        with self.assertRaises(HTTPException):
            prepare("qwen3tts", Synthesis(text="Hello", locale="en", voice={"ref_audio_path": "/etc/passwd", "ref_text": "bad"}))

    async def test_disconnect_before_first_audio_reaps_worker(self):
        req = FakeRequest()
        chunks = self.runtime.chunks(prepare("kokoro", Synthesis(text="stall", locale="en")), req)
        task = asyncio.create_task(anext(chunks))
        while self.runtime.load_ms == 0:
            await asyncio.sleep(0.01)
        process = self.runtime.process
        req.disconnected = True
        start = time.monotonic()
        with self.assertRaises(HTTPException) as exc:
            await task
        self.assertEqual(exc.exception.status_code, 499)
        self.assertLess(time.monotonic() - start, 2.5)
        self.assertIsNone(self.runtime.process)
        self.assertFalse(self.runtime.lock.locked())
        # Process.close has run, so it cannot be alive or orphaned.
        with self.assertRaises(ValueError):
            process.is_alive()

    async def test_generator_close_reaps_streaming_worker_and_reload(self):
        chunks = self.runtime.chunks(prepare("kokoro", Synthesis(text="stall_after_chunk", locale="en")), FakeRequest())
        self.assertEqual(len(await anext(chunks)), 480)
        await chunks.aclose()
        self.assertIsNone(self.runtime.process)
        result = await self.client.post("/v1/synthesize", json={"text": "Hello", "locale": "en"})
        self.assertEqual(result.status_code, 200)

    async def test_queued_disconnect_preserves_active_worker(self):
        chunks = self.runtime.chunks(prepare("kokoro", Synthesis(text="stall_after_chunk", locale="en")), FakeRequest())
        await anext(chunks)
        process = self.runtime.process
        req = FakeRequest()
        req.disconnected = True
        queued = self.runtime.chunks(prepare("kokoro", Synthesis(text="Hello", locale="en")), req)
        with self.assertRaises(HTTPException):
            await anext(queued)
        self.assertIs(self.runtime.process, process)
        self.assertTrue(process.is_alive())
        await chunks.aclose()

    async def test_idle_unload(self):
        await self.client.post("/v1/synthesize", json={"text": "Hello", "locale": "en"})
        self.runtime.idle_unload = 0.1
        task = asyncio.create_task(self.runtime.reap())
        try:
            await asyncio.sleep(0.25)
            self.assertIsNone(self.runtime.process)
            self.assertEqual(self.runtime.info()["load_ms"], 12)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_disk_guard_refuses_load(self):
        with patch("serve.shutil.disk_usage", return_value=SimpleNamespace(free=4_999_999_999)):
            result = await self.client.post("/v1/synthesize", json={"text": "Hello", "locale": "en"})
        self.assertEqual(result.status_code, 507)
        self.assertIsNone(self.runtime.process)
        self.assertFalse(self.runtime.lock.locked())

    async def test_disk_guard_terminates_inflight_worker(self):
        chunks = self.runtime.chunks(prepare("kokoro", Synthesis(text="stall_after_chunk", locale="en")), FakeRequest())
        await anext(chunks)
        self.runtime.last_disk_check = 0
        with patch("serve.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaises(HTTPException) as exc:
                await anext(chunks)
        self.assertEqual(exc.exception.status_code, 507)
        self.assertIsNone(self.runtime.process)
        self.assertFalse(self.runtime.lock.locked())

    async def test_real_http_disconnect_before_headers_and_midstream(self):
        import uvicorn
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, log_level="error"))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            while not server.started:
                await asyncio.sleep(0.01)
            # Drop the TCP connection during generation, before HTTP headers exist.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            payload = json.dumps({"text": "stall", "locale": "en", "stream": True}).encode()
            writer.write(f"POST /v1/synthesize HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n".encode() + payload)
            await writer.drain()
            while self.runtime.load_ms == 0:
                await asyncio.sleep(0.01)
            writer.close()
            await writer.wait_closed()
            start = time.monotonic()
            while self.runtime.process is not None:
                self.assertLess(time.monotonic() - start, 3)
                await asyncio.sleep(0.01)
            while self.runtime.lock.locked():
                await asyncio.sleep(0.01)
            # Drop an actual chunked response after receiving its first audio bytes.
            async with httpx.AsyncClient() as network:
                async with network.stream("POST", f"http://127.0.0.1:{port}/v1/synthesize",
                                          json={"text": "stall_after_chunk", "locale": "en", "stream": True}) as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["transfer-encoding"], "chunked")
                    self.assertEqual(len(await anext(response.aiter_raw())), 480)
                start = time.monotonic()
                while self.runtime.lock.locked():
                    self.assertLess(time.monotonic() - start, 3)
                    await asyncio.sleep(0.01)
                self.assertIsNone(self.runtime.process)
        finally:
            server.should_exit = True
            await asyncio.wait_for(server_task, 5)
            listener.close()

if __name__ == "__main__":
    unittest.main()
