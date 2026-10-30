"""Utterance STT contract; language switch state belongs to one call/session."""
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

LOCALE_LANGUAGE = {'en': 'en', 'zh-CN': 'zh', 'zh-TW': 'zh', 'ja': 'ja', 'yue': 'yue'}
AudioInput = str | Path | bytes | np.ndarray


def validate_locale(locale: str) -> None:
    if locale not in LOCALE_LANGUAGE:
        raise ValueError(f'Unsupported locale: {locale}')


@dataclass
class LanguageState:
    """Create per session, preserve between turns, reset on explicit UI changes."""
    locale: str = 'zh-CN'
    pending_language: str | None = None
    consecutive: int = 0
    chinese_locale: str | None = None

    def __post_init__(self):
        validate_locale(self.locale)
        if self.chinese_locale is None:
            self.chinese_locale = self.locale if self.locale.startswith('zh') else 'zh-CN'
        if self.chinese_locale not in ('zh-CN', 'zh-TW'):
            raise ValueError('chinese_locale must be zh-CN or zh-TW')

    def observe(self, language: str, probability: float) -> str:
        target = {'en': 'en', 'ja': 'ja', 'yue': 'yue', 'zh': 'zh-CN'}.get(language)
        if language == 'zh':
            target = self.chinese_locale
        if target is None or target == self.locale or probability < 0.8:
            self.pending_language, self.consecutive = None, 0
            return self.locale
        self.consecutive = self.consecutive + 1 if self.pending_language == language else 1
        self.pending_language = language
        if self.consecutive >= 2:
            self.locale = target
            self.pending_language, self.consecutive = None, 0
        return self.locale


@dataclass(frozen=True)
class STTResult:
    text: str
    text_raw: str
    locale: str
    lang_detected: str | None
    lang_prob: float | None
    language_mode: str
    language_used: str
    elapsed_ms: float
    audio_duration_s: float
    initial_prompt: str
    reservation_code: str | None = None
    engine: str = 'whisper'

    def to_dict(self) -> dict:
        return asdict(self)


class STTService(Protocol):
    async def transcribe(self, audio: AudioInput, locale: str = 'en', *,
                         auto: bool = False, state: LanguageState | None = None,
                         sample_rate: int = 16000) -> STTResult: ...
