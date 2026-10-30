"""Test-only STT/LLM/TTS fixtures. Never used by server.app or production routing."""
import asyncio
from pathlib import Path
from time import perf_counter

import numpy as np

from server.llm.ollama_client import VoiceAgent
from server.pipeline.turn import Runtime
from server.stt.base import STTResult

INPUTS = {'en': 'Find ZV-4821, surname Carter.',
          'zh-CN': '查询ZV-7305，我姓王。',
          'zh-TW': '查詢ZV-5196，我姓陳。',
          'ja': 'ZV-2610を確認してください。姓は佐藤です。'}
REPLIES = {'en': 'I found your booking. Your hotel is Hanbit Stay Myeongdong.',
           'zh-CN': '已经查到您的预订。车费是78,000韩元。',
           'zh-TW': '已經查到您的訂房。總金額是412,000韓元。',
           'ja': 'ご予約を確認しました。合計130,000ウォンです。'}


class StubSTT:
    def __init__(self, delay=0):
        self.delay = delay
        self.cancelled = False

    async def transcribe(self, pcm, locale):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        text = INPUTS[locale]
        return STTResult(text, text, locale, None, None, 'specified',
                         'zh' if locale.startswith('zh') else locale, 0, len(pcm)/32000, '')


class StubOllama:
    model = 'test-only-stub'

    def __init__(self, locale, delay=0):
        self.locale, self.delay = locale, delay
        self.closed = False

    async def stream_chat(self, messages, tools):
        try:
            await asyncio.sleep(self.delay)
            yield {'message': {'content': REPLIES[self.locale]}}
            yield {'done': True, 'eval_count': 10, 'eval_duration': 1000000}
        finally:
            self.closed = True


class StubTTS:
    def __init__(self, delay=0.005, chunks=3, fail=False):
        self.delay, self.chunks, self.fail = delay, chunks, fail
        self.calls = []
        self.closed = 0
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()

    async def info(self, engine):
        return {'engine': engine, 'locales': list(REPLIES), 'supports_streaming': True}

    async def synthesize(self, engine, text, locale):
        self.calls.append((engine, text, locale))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            for _ in range(self.chunks):
                await asyncio.sleep(self.delay)
                if self.fail:
                    raise RuntimeError('test synthesis failure')
                yield (np.sin(np.arange(480)*2*np.pi*440/24000)*3000).astype('<i2').tobytes()
        finally:
            self.active -= 1
            self.closed += 1


def runtime(log_dir, *, stt=None, tts=None, llm_delay=0):
    log_dir = Path(log_dir)
    agents = []
    def factory(locale):
        agent = VoiceAgent(locale, client=StubOllama(locale, llm_delay), log_path=log_dir/'llm.jsonl')
        agents.append(agent)
        return agent
    result = Runtime(stt=stt or StubSTT(), tts=tts or StubTTS(),
                     agent_factory=factory, log_path=log_dir/'turns.jsonl')
    result.agents = agents
    return result
