"""Local MLX TTS HTTP adapter. See ../PROTOCOL.md for the wire contract."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import logging
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import time
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
MEDIA_TYPE = "audio/L16; rate=24000; channels=1"
ENGINES = {
    "voxcpm2": ("mlx-community/VoxCPM2-bf16", 48000, 8101),
    "qwen3tts": ("mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16", 24000, 8103),
    "kokoro": ("mlx-community/Kokoro-82M-bf16", 24000, 8104),
}
PRESETS = {"en": "af_heart", "zh-CN": "zf_xiaobei", "zh-TW": "zf_xiaobei", "ja": "jf_alpha"}
LOG = logging.getLogger("zivo.tts")


def local_environment():
    # Libraries must never place model downloads, dictionary caches or temp files outside this project.
    for key, value in {
        "HF_HOME": ROOT / "models", "HF_HUB_CACHE": ROOT / "models/hub",
        "XDG_CACHE_HOME": ROOT / ".cache", "TORCH_HOME": ROOT / ".cache/torch",
        "MPLCONFIGDIR": ROOT / ".cache/matplotlib", "TMPDIR": ROOT / ".cache/tmp",
        "NUMBA_CACHE_DIR": ROOT / ".cache/numba",
    }.items():
        os.environ[key] = str(value)
    (ROOT / ".cache/tmp").mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"


class Voice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: str | None = None
    ref_audio_path: str | None = None
    ref_text: str | None = None

    @model_validator(mode="after")
    def valid_variant(self):
        if self.preset is not None:
            if self.ref_audio_path is not None or self.ref_text is not None:
                raise ValueError("Use either a preset or a reference, not both")
        elif not self.ref_audio_path or not self.ref_text or not self.ref_text.strip():
            raise ValueError("Reference voice requires ref_audio_path and nonempty ref_text")
        return self


class Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=2000)
    locale: Literal["en", "zh-CN", "zh-TW", "ja", "yue"]
    voice: Voice | None = None
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    stream: bool = False

    @model_validator(mode="after")
    def nonempty(self):
        if not self.text.strip():
            raise ValueError("text must contain non-whitespace characters")
        return self


def reference_voice(locale):
    manifest = json.loads((ROOT / "data/refs/MANIFEST.json").read_text())
    ref_locale = manifest["locale_reference"][locale]
    ref = next(r for r in manifest["references"] if r["locale"] == ref_locale)
    return {"ref_audio_path": str(ROOT / ref["audio_path"]),
            "ref_text": (ROOT / ref["text_path"]).read_text().strip()}


def prepare(engine, body):
    if body.locale == "yue" and engine != "voxcpm2":
        raise HTTPException(422, "This engine does not support yue")
    data = body.model_dump()
    voice = body.voice.model_dump(exclude_none=True) if body.voice else (
        {"preset": PRESETS[body.locale]} if engine == "kokoro" else reference_voice(body.locale))
    if engine == "kokoro":
        if voice.get("preset") != PRESETS[body.locale]:
            raise HTTPException(422, f"Expected preset {PRESETS[body.locale]} for this locale")
    else:
        if "preset" in voice:
            raise HTTPException(422, "This cloning model requires reference audio and text")
        path = (ROOT / voice["ref_audio_path"]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise HTTPException(422, "Reference must be an existing file inside the project")
        voice["ref_audio_path"] = str(path)
    data["voice"] = voice
    return data


def inference_worker(pipe, engine, model_path):
    """One persistent, killable inference process; no GPU work on the HTTP event loop."""
    local_environment()
    started = time.perf_counter()
    try:
        import resource
        import mlx.core as mx
        import numpy as np
        from scipy.signal import resample_poly
        from mlx_audio.tts.utils import load_model
        mx.set_cache_limit(256 * 1024**2)
        model = load_model(Path(model_path))
        def memory():
            # ru_maxrss is bytes on macOS. MLX and RSS overlap: never sum these two.
            return max(int(mx.get_peak_memory()), int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        pipe.send(("ready", {"load_ms": (time.perf_counter() - started) * 1000,
                             "peak_mem_bytes": memory()}))
        while True:
            data = pipe.recv()
            began = time.perf_counter()
            try:
                import random
                random.seed(data["seed"])
                np.random.seed(data["seed"])
                mx.random.seed(data["seed"])
                voice = data["voice"]
                kwargs = {"text": data["text"]}
                if engine == "voxcpm2":
                    # mlx-audio's ref_text alias is unused by VoxCPM2: use continuation
                    # prompt_audio + prompt_text so the supplied transcript actually conditions it.
                    kwargs.update(prompt_audio=voice["ref_audio_path"], prompt_text=voice["ref_text"])
                elif engine == "qwen3tts":
                    kwargs.update(ref_audio=voice["ref_audio_path"], ref_text=voice["ref_text"],
                                  lang_code={"en": "english", "zh-CN": "chinese", "zh-TW": "chinese", "ja": "japanese"}[data["locale"]],
                                  stream=data["stream"], streaming_interval=0.32)
                else:
                    if data["locale"] == "zh-TW":
                        from opencc import OpenCC
                        kwargs["text"] = OpenCC("t2s").convert(kwargs["text"])
                    kwargs.update(voice=voice["preset"], lang_code={"en": "a", "zh-CN": "z", "zh-TW": "z", "ja": "j"}[data["locale"]])
                count = 0
                for result in model.generate(**kwargs):
                    audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                    if not np.isfinite(audio).all():
                        raise ValueError("Model returned non-finite samples")
                    if result.sample_rate != 24000:
                        from math import gcd
                        divisor = gcd(result.sample_rate, 24000)
                        audio = resample_poly(audio, 24000 // divisor, result.sample_rate // divisor)
                    pcm = np.rint(np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
                    # Bound pipe payloads / client buffering to 100 ms, keep even-byte boundaries.
                    for offset in range(0, len(pcm), 4800):
                        pipe.send(("audio", pcm[offset:offset + 4800]))
                        count += len(pcm[offset:offset + 4800])
                if not count:
                    raise ValueError("Model returned no audio")
                mx.clear_cache()
                pipe.send(("done", {"synth_ms": (time.perf_counter() - began) * 1000,
                                    "peak_mem_bytes": memory()}))
            except Exception as exc:
                LOG.exception("Synthesis failed")
                pipe.send(("error", f"{type(exc).__name__}: {exc}"))
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        LOG.exception("Model load failed")
        pipe.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        pipe.close()


class Runtime:
    def __init__(self, engine, idle_unload=600, worker_target=inference_worker):
        self.engine, self.idle_unload, self.worker_target = engine, idle_unload, worker_target
        self.lock = asyncio.Lock()
        self.process = self.pipe = None
        self.load_ms = 0.0
        self.peak_mem_bytes = 0
        self.last_used = time.monotonic()
        self.synth_ms = 0.0
        self.last_disk_check = 0.0

    def check_disk(self):
        if shutil.disk_usage(ROOT).free < 5_000_000_000:
            raise HTTPException(507, "Less than 5 GB disk free; inference stopped to avoid swap exhaustion")

    def info(self):
        return dict(engine=self.engine, model_id=ENGINES[self.engine][0],
                    locales=list(PRESETS) + (["yue"] if self.engine == "voxcpm2" else []),
                    supports_streaming=self.engine == "qwen3tts",
                    native_sample_rate=ENGINES[self.engine][1], load_ms=self.load_ms,
                    peak_mem_bytes=self.peak_mem_bytes)

    async def receive(self, request=None):
        while True:
            if time.monotonic() - self.last_disk_check > 1:
                self.check_disk()
                self.last_disk_check = time.monotonic()
            if request and await request.is_disconnected():
                raise HTTPException(499, "Client disconnected")
            if self.pipe.poll():
                try:
                    return self.pipe.recv()
                except EOFError as exc:
                    raise HTTPException(503, "Inference process exited") from exc
            if not self.process.is_alive():
                raise HTTPException(503, f"Inference process exited ({self.process.exitcode})")
            await asyncio.sleep(0.02)

    async def load(self, request=None):
        self.check_disk()
        if self.process is not None:
            return
        paths = json.loads((ROOT / "models/paths.json").read_text())
        model_path = ROOT / paths[self.engine]
        if not model_path.is_dir():
            raise HTTPException(503, f"Local model not found: {model_path}")
        context = mp.get_context("spawn")
        self.pipe, child = context.Pipe()
        self.process = context.Process(target=self.worker_target, args=(child, self.engine, str(model_path)), daemon=True)
        self.process.start()
        child.close()
        kind, data = await self.receive(request)
        if kind != "ready":
            raise HTTPException(503, data)
        self.load_ms = data["load_ms"]
        self.peak_mem_bytes = max(self.peak_mem_bytes, data["peak_mem_bytes"])

    async def stop(self):
        process, pipe = self.process, self.pipe
        self.process = self.pipe = None
        if process:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 2)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join)
            process.close()
        if pipe:
            pipe.close()

    async def acquire(self, request):
        while True:
            if await request.is_disconnected():
                raise HTTPException(499, "Client disconnected while queued")
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=0.1)
                return
            except TimeoutError:
                pass

    async def chunks(self, data, request):
        await self.acquire(request)
        complete = False
        try:
            await self.load(request)
            self.pipe.send(data)
            while True:
                kind, payload = await self.receive(request)
                if kind == "audio":
                    yield payload
                elif kind == "done":
                    self.synth_ms = payload["synth_ms"]
                    self.peak_mem_bytes = max(self.peak_mem_bytes, payload["peak_mem_bytes"])
                    complete = True
                    return
                else:
                    raise HTTPException(500, payload)
        finally:
            # A disconnected caller must not leave GPU generation running, even when
            # upstream exposes only one final yield (VoxCPM2). Reload on the next request.
            if not complete:
                # Starlette cancels the streaming task; shield cleanup from its scope.
                import anyio
                with anyio.CancelScope(shield=True):
                    await self.stop()
                LOG.info("Inference stopped after cancellation/failure: %s", self.engine)
            self.last_used = time.monotonic()
            self.lock.release()

    async def reap(self):
        while True:
            await asyncio.sleep(min(10, max(0.1, self.idle_unload)))
            if self.idle_unload > 0 and not self.lock.locked():
                async with self.lock:
                    if time.monotonic() - self.last_used >= self.idle_unload:
                        await self.stop()


def create_app(engine="kokoro", lazy=False, idle_unload=600, worker_target=inference_worker):
    local_environment()
    runtime = Runtime(engine, idle_unload, worker_target)

    @asynccontextmanager
    async def lifespan(app):
        try:
            if not lazy:
                await runtime.load()
            reaper = asyncio.create_task(runtime.reap())
            try:
                yield
            finally:
                reaper.cancel()
                try:
                    await reaper
                except asyncio.CancelledError:
                    pass
        finally:
            await runtime.stop()

    app = FastAPI(title=f"ZIVO {engine}", lifespan=lifespan)
    app.state.runtime = runtime

    @app.get("/v1/info")
    async def info():
        return runtime.info()

    @app.post("/v1/warmup")
    async def warmup(request: Request):
        body = Synthesis(text="Hello, how can I help you?", locale="en")
        async for _ in runtime.chunks(prepare(engine, body), request):
            pass
        return {**runtime.info(), "warmed_up": True, "warmup_ms": runtime.synth_ms}

    @app.post("/v1/synthesize")
    async def synthesize(body: Synthesis, request: Request):
        data = prepare(engine, body)
        chunks = runtime.chunks(data, request)
        if not body.stream:
            pcm = b"".join([chunk async for chunk in chunks])
            return Response(pcm, media_type=MEDIA_TYPE, headers={"X-Synth-Ms": f"{runtime.synth_ms:.3f}"})
        # Prime before sending HTTP 200 so load and first-generation errors are JSON errors.
        first = await anext(chunks)
        async def stream():
            try:
                yield first
                async for chunk in chunks:
                    yield chunk
            finally:
                await chunks.aclose()
        return StreamingResponse(stream(), media_type=MEDIA_TYPE)

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=ENGINES, required=True)
    parser.add_argument("--port", type=int)
    parser.add_argument("--lazy", action="store_true")
    parser.add_argument("--idle-unload", type=float, default=600)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.engine, args.lazy, args.idle_unload),
                host="127.0.0.1", port=args.port or ENGINES[args.engine][2])


if __name__ == "__main__":
    main()
