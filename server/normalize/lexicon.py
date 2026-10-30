"""Longest-match, single-pass pronunciation dictionary shared by STT and TTS."""
from functools import lru_cache
from pathlib import Path
import re
import yaml

LEXICON_PATH = Path(__file__).resolve().parents[2] / 'data' / 'lexicon.yaml'
LOCALES = ('en', 'zh-CN', 'zh-TW', 'ja', 'yue')


def validate_locale(locale: str) -> None:
    if locale not in LOCALES:
        raise ValueError(f'Unsupported locale: {locale}')


@lru_cache(maxsize=1)
def load_lexicon() -> list[dict]:
    return yaml.safe_load(LEXICON_PATH.read_text(encoding='utf-8'))


def display_name(lexicon_id: str, locale: str) -> str:
    validate_locale(locale)
    return next(e['display'][locale] for e in load_lexicon() if e['id'] == lexicon_id)


def initial_prompt(locale: str) -> str:
    validate_locale(locale)
    return ', '.join(e['display'][locale] for e in load_lexicon())


@lru_cache(maxsize=5)
def _replacements(locale: str):
    validate_locale(locale)
    mapping = {}
    for e in load_lexicon():
        target = e.get('tts', {}).get(locale, e['display'][locale])
        for alias in [e['ko'], *e['display'].values(), *e.get('aliases', [])]:
            mapping[alias.casefold()] = target
    # ASCII word boundaries avoid replacing names inside other English words;
    # Chinese/Japanese names need no spaces and must remain matchable inline.
    parts = []
    for alias in sorted(mapping, key=len, reverse=True):
        left = r'(?<![A-Za-z])' if alias[0].isascii() and alias[0].isalpha() else ''
        right = r'(?![A-Za-z])' if alias[-1].isascii() and alias[-1].isalpha() else ''
        parts.append(left + re.escape(alias) + right)
    return re.compile('|'.join(parts), re.I), mapping


def replace_lexicon(text: str, locale: str) -> str:
    pattern, mapping = _replacements(locale)
    return pattern.sub(lambda m: mapping[m[0].casefold()], text)
