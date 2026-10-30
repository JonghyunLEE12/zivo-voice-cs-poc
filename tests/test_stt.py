import asyncio
import threading
import time

import numpy as np
import pytest

from server.stt.base import LanguageState
from server.stt.mlx_whisper_stt import MLXWhisperSTT
from server.stt.script import normalize_script


def test_switch_requires_two_consecutive_confident_turns():
    state = LanguageState('en')
    assert state.observe('ja', .9) == 'en'
    assert state.observe('ja', .79) == 'en'
    assert state.observe('ja', .8) == 'en'
    assert state.observe('ja', .8) == 'ja'
    assert state.observe('ko', .99) == 'ja'
    assert state.observe('zh', .9) == 'ja'
    assert state.observe('zh', .9) == 'zh-CN'


def test_chinese_locale_is_never_inferred_from_voice():
    state = LanguageState('zh-TW')
    for _ in range(4):
        assert state.observe('zh', .99) == 'zh-TW'
    state.observe('en', .99)
    assert state.observe('en', .99) == 'en'
    state.observe('zh', .99)
    assert state.observe('zh', .99) == 'zh-TW'
    assert normalize_script('请帮我确认预订，软件和出租车。', 'zh-TW') == '請幫我確認預訂，軟體和計程車。'
    assert normalize_script('請幫我確認飯店。', 'zh-CN') == '请帮我确认饭店。'
    assert normalize_script('明洞駅', 'ja') == '明洞駅'


def test_pcm_contract_and_invalid_inputs():
    pcm = np.array([-32768, 0, 32767], dtype='<i2').tobytes()
    assert np.allclose(MLXWhisperSTT._audio(pcm, 16000), [-1, 0, 32767 / 32768])
    for data, rate in [(b'x', 16000), (pcm, 24000), (b'', 16000),
                       (np.array([np.nan]), 16000), (np.zeros((2, 2)), 16000),
                       (np.array([10.0]), 16000), (np.array([1], dtype='int16'), 16000)]:
        with pytest.raises(ValueError):
            MLXWhisperSTT._audio(data, rate)


def test_local_model_and_dictionary():
    engine = MLXWhisperSTT()
    assert engine.model_path.is_dir()
    assert 'Euljiro 3-ga Station' in engine.initial_prompt('en')
    assert '仁川國際機場' in engine.initial_prompt('zh-TW')
    with pytest.raises(FileNotFoundError):
        MLXWhisperSTT('/not-installed')
    with pytest.raises(ValueError):
        engine.transcribe_sync(b'\0\0', auto=True)


@pytest.mark.asyncio
async def test_cancelled_decode_cannot_mutate_session(monkeypatch):
    engine = MLXWhisperSTT()
    started, finished = threading.Event(), threading.Event()
    def decode(audio, locale, *, auto, state, sample_rate):
        started.set()
        time.sleep(.05)
        state.observe('ja', .99)
        state.observe('ja', .99)
        finished.set()
    monkeypatch.setattr(engine, 'transcribe_sync', decode)
    state = LanguageState('en')
    task = asyncio.create_task(engine.transcribe(b'\0\0', auto=True, state=state))
    while not started.is_set():
        await asyncio.sleep(.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    while not finished.is_set():
        await asyncio.sleep(.001)
    assert state.locale == 'en'
