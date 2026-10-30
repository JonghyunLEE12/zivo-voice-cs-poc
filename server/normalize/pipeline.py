"""Pure text normalization; display text never changes at the caller."""
import re
import unicodedata
from .lexicon import replace_lexicon, validate_locale
from .codes import speak_codes
from . import num_en, num_ja, num_zh


def normalize(text: str, locale: str, level: str = 'full') -> str:
    validate_locale(locale)
    if level not in ('raw', 'lex', 'full'):
        raise ValueError(f'Unsupported normalization level: {level}')
    if level == 'raw':
        return text
    text = replace_lexicon(text, locale)
    if level == 'lex':
        return text
    text = speak_codes(text, locale)
    if locale == 'en':
        text = num_en.normalize_numbers(text)
    elif locale == 'ja':
        text = num_ja.normalize_numbers(text)
    else:
        text = num_zh.normalize_numbers(text, locale)
    text = re.sub(r'[()（）\[\]{}／/]', ' ', text)
    text = ''.join(c for c in text if not (unicodedata.category(c) == 'So' or c in '\ufe0f\u200d\u20e3' or '\U0001f3fb' <= c <= '\U0001f3ff'))
    return re.sub(r'\s+', ' ', text).strip()


def variants(text: str, locale: str) -> dict[str, str]:
    return {'display_text':text, **{f'tts_text_{level}':normalize(text, locale, level) for level in ('raw','lex','full')}}
