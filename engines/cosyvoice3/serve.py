"""Local CosyVoice3 HTTP engine; run with uv run python serve.py --port 8102."""
import argparse
import asyncio
from contextlib import asynccontextmanager
import logging
import queue
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from backend import Backend, LOCALES, MODEL_ID

MEDIA_TYPE = 'audio/L16; rate=24000; channels=1'


class Voice(BaseModel):
    ref_audio_path: str | None = None
    ref_text: str | None = None
    preset: str | None = None


class Synthesis(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    locale: str
    voice: Voice | None = None
    seed: int = Field(default=42, ge=0, le=2**32 - 1)
    stream: bool = False


class Engine:
    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        self.active = 0
        self.cancelled = 0
        self.completed = 0

    async def audio(self, spec, request=None):
        cancelled = threading.Event()
        messages = queue.Queue(maxsize=2)
        def send(value):
            while not cancelled.is_set():
                try:
                    messages.put(value, timeout=.05)
                    return
                except queue.Full:
                    pass
        def work():
            while not self.lock.acquire(timeout=.05):
                if cancelled.is_set():
                    return
            try:
                if cancelled.is_set():
                    return
                self.active += 1
                for pcm in self.backend.generate(spec.text, spec.locale,
                        spec.voice.model_dump() if spec.voice else None,
                        spec.seed, spec.stream, cancelled):
                    if cancelled.is_set():
                        break
                    if pcm:
                        send(pcm)
                if cancelled.is_set():
                    self.cancelled += 1
                else:
                    self.completed += 1
                    send(None)
            except Exception as exc:
                logging.exception('CosyVoice synthesis failed')
                send(exc)
            finally:
                self.active = 0
                self.last_used = time.monotonic()
                self.lock.release()
        # Worker owns the lock until native computation actually stops, even if HTTP disconnects.
        thread = threading.Thread(target=work, daemon=True, name='cosyvoice-inference')
        thread.start()
        try:
            while True:
                if request is not None and await request.is_disconnected():
                    return
                try:
                    item = messages.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.01)
                    continue
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            cancelled.set()

    def unload_idle(self, seconds):
        if time.monotonic() - self.last_used < seconds or not self.lock.acquire(blocking=False):
            return
        try:
            if self.backend.model is not None:
                self.backend.unload()
        finally:
            self.lock.release()


def create_app(device='auto', lazy=False, idle_unload=600, threads=4, backend=None):
    engine = Engine(backend or Backend(device, threads))
    @asynccontextmanager
    async def lifespan(app):
        if not lazy:
            await asyncio.to_thread(engine.backend.load)
        async def reap():
            while True:
                await asyncio.sleep(min(30, max(1, idle_unload)))
                if idle_unload > 0:
                    await asyncio.to_thread(engine.unload_idle, idle_unload)
        task = asyncio.create_task(reap())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    app = FastAPI(title='ZIVO CosyVoice3', lifespan=lifespan)
    app.state.engine = engine

    @app.get('/v1/info')
    async def info():
        b = engine.backend
        return dict(engine='cosyvoice3', model_id=MODEL_ID, locales=LOCALES,
                    supports_streaming=True, native_sample_rate=24000, load_ms=b.load_ms,
                    peak_mem_bytes=b.peak_mem_bytes, device=b.device, loaded=b.model is not None,
                    active_requests=engine.active, cancelled_requests=engine.cancelled,
                    completed_requests=engine.completed,
                    memory_note='Peak observed max(RSS, MPS driver allocation); unified-memory figures overlap.',
                    reference_limitations=['대만 억양 참조 없음'])

    @app.post('/v1/warmup')
    async def warmup(request: Request):
        started = time.perf_counter()
        spec = Synthesis(text='Hello, how can I help you today?', locale='en')
        size = 0
        try:
            async for pcm in engine.audio(spec, request):
                size += len(pcm)
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc
        return dict(ok=size > 0, warmup_ms=(time.perf_counter()-started)*1000,
                    audio_seconds=size/48000, **await info())

    @app.post('/v1/synthesize')
    async def synthesize(spec: Synthesis, request: Request):
        try:
            if not spec.text.strip():
                raise ValueError('Text must not be blank')
            engine.backend.resolve_voice(spec.locale, spec.voice.model_dump() if spec.voice else None)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        started = time.perf_counter()
        audio = engine.audio(spec, request)
        try:
            first = await anext(audio, None)
        except Exception as exc:
            await audio.aclose()
            raise HTTPException(503, str(exc)) from exc
        if first is None:
            await audio.aclose()
            raise HTTPException(503, 'Synthesis returned no audio or client disconnected')
        if not spec.stream:
            chunks = [first]
            try:
                async for pcm in audio:
                    chunks.append(pcm)
            except Exception as exc:
                raise HTTPException(503, str(exc)) from exc
            finally:
                await audio.aclose()
            return Response(b''.join(chunks), media_type=MEDIA_TYPE,
                            headers={'X-Synth-Ms': f'{(time.perf_counter()-started)*1000:.3f}'})
        async def stream_audio():
            try:
                yield first
                async for pcm in audio:
                    yield pcm
            finally:
                await audio.aclose()
        return StreamingResponse(stream_audio(), media_type=MEDIA_TYPE)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8102)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--device', choices=['auto', 'mps', 'cpu'], default='auto')
    parser.add_argument('--lazy', action='store_true')
    parser.add_argument('--idle-unload', type=float, default=600)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.device, args.lazy, args.idle_unload, args.threads),
                host=args.host, port=args.port)
