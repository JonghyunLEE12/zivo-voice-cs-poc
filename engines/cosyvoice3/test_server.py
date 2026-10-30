import asyncio
import threading
import time
from fastapi.testclient import TestClient
from serve import create_app, Engine, Synthesis
from backend import Backend


class FakeBackend:
    device = 'cpu'
    model = None
    load_ms = 0
    peak_mem_bytes = 0
    def load(self):
        self.model = object()
        self.load_ms = 10
    def unload(self):
        self.model = None
    def resolve_voice(self, locale, voice):
        if locale not in ['en', 'zh-CN', 'zh-TW', 'ja', 'yue']:
            raise ValueError('Unsupported locale')
    def generate(self, text, locale, voice, seed, stream, cancel):
        self.load()
        if text == 'fail':
            raise RuntimeError('deliberate model failure')
        for _ in range(10):
            if cancel.is_set():
                return
            time.sleep(.01)
            yield b'\x10\x00' * 240


def test_contract_and_validation():
    with TestClient(create_app(lazy=True, backend=FakeBackend())) as client:
        info = client.get('/v1/info').json()
        assert info['loaded'] is False
        assert info['locales'] == ['en', 'zh-CN', 'zh-TW', 'ja', 'yue']
        for stream in [False, True]:
            res = client.post('/v1/synthesize', json={'text':'hello', 'locale':'en', 'stream':stream})
            assert res.status_code == 200
            assert res.headers['content-type'] == 'audio/L16; rate=24000; channels=1'
            assert len(res.content) == 4800
            assert ('x-synth-ms' in res.headers) == (not stream)
        for payload in [{'text':' ', 'locale':'en'}, {'text':'hi', 'locale':'ko'}, {'text':'hi', 'locale':'en', 'seed':-1}]:
            assert client.post('/v1/synthesize', json=payload).status_code == 422
        assert client.post('/v1/synthesize', json={'text':'fail', 'locale':'en'}).status_code == 503
        assert client.post('/v1/warmup').json()['ok']


def test_cancel_stops_producer_and_releases_lock():
    async def run():
        engine = Engine(FakeBackend())
        stream = engine.audio(Synthesis(text='hello', locale='en', stream=True))
        assert await anext(stream)
        await stream.aclose()
        for _ in range(100):
            if not engine.lock.locked():
                break
            await asyncio.sleep(.01)
        assert not engine.lock.locked()
        assert engine.cancelled == 1
        assert len(b''.join([x async for x in engine.audio(Synthesis(text='hi', locale='en'))])) == 4800
        assert engine.completed == 1
    asyncio.run(run())


def test_reference_mapping_and_path_guards():
    backend = Backend('cpu')
    assert backend.resolve_voice('zh-CN', None) == backend.resolve_voice('zh-TW', None)
    for voice in [{'ref_audio_path':'/etc/passwd','ref_text':'test'}, {'preset':'bad'}, {'ref_text':'missing audio'}, {'ref_audio_path':'data/refs/en/ref.wav'}]:
        try:
            backend.resolve_voice('en', voice)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid reference accepted')


def test_idle_unload_never_races_active_generation():
    backend = FakeBackend(); backend.load()
    engine = Engine(backend); engine.last_used = 0
    with engine.lock:
        engine.unload_idle(1)
        assert backend.model is not None
    engine.unload_idle(1)
    assert backend.model is None
