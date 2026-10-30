# T2 normalization integration

```python
from server.normalize.pipeline import normalize, variants
from server.normalize.codes import parse_spoken_code
from server.normalize.lexicon import initial_prompt, display_name, load_lexicon

normalize('ZV-4821 at Hanbit Stay Myeongdong', 'en', 'full')
parse_spoken_code('ゼットブイ よんはちにいち', 'ja')  # ZV-4821
initial_prompt('zh-TW')  # comma-separated display names for Whisper
```

`normalize(text, locale, level)` returns a string for `raw`, `lex`, `full`.
Supported locales: en, zh-CN, zh-TW, ja, yue. `raw` is byte-for-byte unchanged;
`lex` performs longest-match single-pass replacement; `full` then reads reservation
codes, money, dates/times, percentages/numbers and cleans symbols. Display text
remains the caller's original input. English written date ordinals (November 3rd)
are deliberately left alone, as required by DESIGN §5.2.

`variants` returns `display_text`, `tts_text_raw`, `tts_text_lex`, `tts_text_full`.
`parse_spoken_code` returns one canonical ZV-four-digit code or None; it rejects
multiple distinct codes, extra digits, missing prefixes and ASCII suffixes.
Zeroes are retained. Kanji/kana and English spoken digits are supported.

`data/lexicon.yaml` is the complete 13-entry DESIGN §5.1 dictionary.
`data/blind/sentences.yaml` is a YAML sequence of `{id, locale, text}` objects:
8 each for en/zh-CN/zh-TW/ja and 3 for yue, totaling 35.

```sh
uv run python scripts/normalize_demo.py
uv run python scripts/normalize_demo.py --json
uv run python -m pytest tests/test_normalize.py -q
```

Numbers use num2words 0.5.14 (LGPL), cn2an 0.5.24 (MIT), and local Japanese rules.
Licenses were checked in installed package metadata and the PyPI project pages:
https://pypi.org/project/num2words/ and https://pypi.org/project/cn2an/.
PyYAML is MIT, OpenCC's Python reimplementation is Apache licensed, httpx is
BSD-3-Clause and Jinja2 is BSD licensed. No model files are included here.

Scope: deterministic speech preprocessing, not a general multilingual grammar
engine or a learned pronunciation guarantee. Cantonese uses traditional written
numerals for the optional set, with pronunciation delegated to the yue engine.
Apply Kokoro's zh-TW-to-simplified adapter conversion at the engine boundary, not
inside the shared normalizer, to preserve all other engines' traditional input.
