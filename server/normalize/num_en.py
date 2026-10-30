"""US English speech numbers; written English month ordinals stay unchanged."""
import re
from datetime import date
from num2words import num2words

NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'


def cardinal(value: str | int) -> str:
    return num2words(str(value).replace(',', ''), lang='en').replace(' and ', ' ').replace(', ', ' ')


def normalize_numbers(text: str) -> str:
    def iso(m):
        try:
            d = date.fromisoformat(m[0])
        except ValueError:
            return m[0]
        return f'{d.strftime("%B")} {num2words(d.day, to="ordinal")}, {cardinal(d.year)}'
    text = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', iso, text)
    text = re.sub(r'₩\s*(' + NUMBER + r')', lambda m: cardinal(m[1]) + ' won', text)
    text = re.sub(r'(' + NUMBER + r')\s*(?:KRW|won)\b', lambda m: cardinal(m[1]) + ' won', text, flags=re.I)
    def time(m):
        hour, minute = int(m[1]), int(m[2])
        if hour > 23 or minute > 59:
            return m[0]
        suffix = (' ' + m[3].lower()[0] + ' m') if m[3] else ''
        mins = (' oh ' + cardinal(minute)) if 0 < minute < 10 else (' ' + cardinal(minute) if minute else '')
        return cardinal(hour) + mins + suffix
    text = re.sub(r'\b(\d{1,2}):(\d{2})(?:\s*([AP]M))?\b', time, text, flags=re.I)
    text = re.sub(r'(' + NUMBER + r')\s*%', lambda m: cardinal(m[1]) + ' percent', text)
    return re.sub(r'(?<![\w])' + NUMBER + r'(?![\w])', lambda m: cardinal(m[0]), text)
