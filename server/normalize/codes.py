"""Reservation codes: exact four digits, preserve leading zero, reject ambiguity."""
import re
import unicodedata
from .lexicon import validate_locale

EN = 'zero one two three four five six seven eight nine'.split()
JA = 'ゼロ いち に さん よん ご ろく なな はち きゅう'.split()
ZH = '零一二三四五六七八九'
_DIGITS = {str(i): str(i) for i in range(10)}
for words in [EN, JA, list(ZH), 'れい いち に さん し ご ろく しち はち く'.split()]:
    _DIGITS.update({word.casefold(): str(i) for i, word in enumerate(words)})
_DIGITS.update({'〇': '0', '○': '0', '幺': '1', '两': '2', '兩': '2', 'ぜろ': '0'})
_TOKEN = re.compile('|'.join(map(re.escape, sorted(_DIGITS, key=len, reverse=True))), re.I)
_PREFIX = re.compile(r'(?<![A-Za-z])(?:z\s*[- ]?\s*v|zee\s+vee|zed\s+vee|ゼット\s*ブイ|ぜっと\s*ぶい)\s*[-:：]?\s*', re.I)


def parse_spoken_code(text: str, locale: str = 'en') -> str | None:
    validate_locale(locale)
    text = unicodedata.normalize('NFKC', text)
    text = ''.join(chr(ord(c)-0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)
    codes = []
    for prefix in _PREFIX.finditer(text):
        pos, digits = prefix.end(), ''
        last_end = pos
        while True:
            sep = re.match(r'[\s,、-]*', text[pos:])
            pos += sep.end()
            match = _TOKEN.match(text, pos)
            if not match:
                break
            # Do not consume English number words embedded in unrelated words.
            if match[0].isascii() and match[0].isalpha() and match.end() < len(text) and text[match.end()].isascii() and text[match.end()].isalpha():
                break
            digits += _DIGITS[match[0].casefold()]
            pos = match.end()
            last_end = pos
        if len(digits) == 4 and not (last_end < len(text) and text[last_end].isascii() and text[last_end].isalnum()):
            codes.append('ZV-' + digits)
    return codes[0] if len(set(codes)) == 1 else None


def speak_codes(text: str, locale: str) -> str:
    validate_locale(locale)
    def repl(m):
        if locale == 'en':
            return 'Z V ' + ' '.join(EN[int(d)] for d in m[1])
        if locale == 'ja':
            return 'ゼット ブイ ' + ' '.join(JA[int(d)] for d in m[1])
        return 'Z V ' + ''.join(ZH[int(d)] for d in m[1])
    return re.sub(r'(?<![A-Za-z0-9])ZV-(\d{4})(?![A-Za-z0-9])', repl, text, flags=re.I)
