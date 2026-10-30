"""Cancellable local engine HTTP adapter; PCM16LE/24 kHz on every yield."""
from contextlib import aclosing
import asyncio
import json

import httpx

from server.config import ENGINE_URLS, ROOT
from server.normalize.pipeline import normalize

FILLERS = {
    'en': 'Let me check that for you.',
    'zh-CN': '我帮您查一下。',
    'zh-TW': '我幫您查一下。',
    'ja': '確認いたしますので、少々お待ちください。',
    'yue': '等我幫你查一查。',
}


def voice(engine, locale):
    if engine == 'kokoro':
        return {'preset': {'en': 'af_heart', 'zh-CN': 'zf_xiaobei',
                           'zh-TW': 'zf_xiaobei', 'ja': 'jf_alpha'}[locale]}
    manifest = json.loads((ROOT / 'data/refs/MANIFEST.json').read_text())
    reference = next(r for r in manifest['references']
                     if r['locale'] == manifest['locale_reference'][locale])
    return {'ref_audio_path': str(ROOT / reference['audio_path']),
            'ref_text': (ROOT / reference['text_path']).read_text().strip()}


class TTSClient:
    def __init__(self, urls=None, transport=None):
        self.urls = urls or ENGINE_URLS
        self.transport = transport

    async def info(self, engine):
        async with httpx.AsyncClient(timeout=10, transport=self.transport, trust_env=False) as client:
            response = await client.get(self.urls[engine] + '/v1/info')
            response.raise_for_status()
            result = response.json()
            if result['engine'] != engine:
                raise RuntimeError('Engine identity mismatch')
            return result

    async def synthesize(self, engine, text, locale):
        payload = {'text': text, 'locale': locale, 'voice': voice(engine, locale),
                   'seed': 42, 'stream': True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5),
                                    transport=self.transport, trust_env=False) as client:
            async with client.stream('POST', self.urls[engine] + '/v1/synthesize', json=payload) as response:
                response.raise_for_status()
                content_type = response.headers.get('content-type', '').lower().replace(' ', '')
                if content_type != 'audio/l16;rate=24000;channels=1':
                    raise RuntimeError('Expected PCM16LE mono 24000 Hz engine response')
                pending = b''
                total = 0
                async for data in response.aiter_bytes():
                    pending += data
                    even = len(pending) // 2 * 2
                    for offset in range(0, even, 48000):
                        chunk = pending[offset:min(offset + 48000, even)]
                        total += len(chunk)
                        yield chunk
                    pending = pending[even:]
                if pending or not total:
                    raise RuntimeError('Truncated or empty engine PCM')


class FillerCache:
    """Warm only the selected engine/locale before session.ready, once per process."""
    def __init__(self):
        self.entries = {}
        self.lock = asyncio.Lock()

    async def prepare(self, tts, engine, locale):
        key = engine, locale
        async with self.lock:
            if key not in self.entries:
                data = bytearray()
                async with aclosing(tts.synthesize(engine, normalize(FILLERS[locale], locale), locale)) as stream:
                    async for chunk in stream:
                        data.extend(chunk)
                        if len(data) > 24000 * 2 * 30:
                            raise RuntimeError('Filler exceeds 30 seconds')
                if not data:
                    raise RuntimeError('Empty filler')
                self.entries[key] = bytes(data)
        return self.entries[key]
