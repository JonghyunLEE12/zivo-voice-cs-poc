"""Preserve raw transcription at the caller; only normalize its display copy."""
from functools import lru_cache

from opencc import OpenCC

from .base import validate_locale


@lru_cache(maxsize=2)
def _converter(config: str) -> OpenCC:
    return OpenCC(config)


def normalize_script(text: str, locale: str) -> str:
    validate_locale(locale)
    if locale == 'zh-TW':
        return _converter('s2twp').convert(text)
    if locale == 'zh-CN':
        return _converter('t2s').convert(text)
    return text
