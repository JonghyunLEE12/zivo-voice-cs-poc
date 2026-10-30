"""Mainland/Taiwan written speech numerals (also traditional Cantonese input)."""
import re
import cn2an

NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'


def cardinal(value: str | int, locale: str = 'zh-CN') -> str:
    value = str(value).replace(',', '')
    result = cn2an.an2cn(value, 'low')
    if locale in ('zh-TW', 'yue'):
        result = result.translate(str.maketrans('万亿点', '萬億點'))
        result = re.sub(r'二(?=[百千])', '兩', result)
        result = re.sub(r'(?<![一二三四五六七八九十百千])二(?=[萬億])', '兩', result)
    return result


def normalize_numbers(text: str, locale: str = 'zh-CN') -> str:
    n = lambda value: cardinal(value, locale)
    text = re.sub(r'(\d{4})-(\d{2})-(\d{2})', lambda m: ''.join('零一二三四五六七八九'[int(d)] for d in m[1]) + '年' + n(int(m[2])) + '月' + n(int(m[3])) + '日', text)
    text = re.sub(r'(\d{4})年', lambda m: ''.join('零一二三四五六七八九'[int(d)] for d in m[1]) + '年', text)
    unit = '韩元' if locale == 'zh-CN' else '韓元'
    text = re.sub(r'₩\s*(' + NUMBER + ')', lambda m: n(m[1]) + unit, text)
    text = re.sub(r'(' + NUMBER + r')\s*%', lambda m: '百分之' + n(m[1]), text)
    def time(m):
        period, h, minute = m[1] or '', int(m[2]), int(m[3])
        if h > 23 or minute > 59:
            return m[0]
        if period:
            h = h % 12 or 12
        point = '点' if locale == 'zh-CN' else '點'
        tail = '半' if minute == 30 else (n(minute) + '分' if minute else '')
        return period + ('兩' if h == 2 and locale in ('zh-TW', 'yue') else n(h)) + point + tail
    text = re.sub(r'(凌晨|早上|上午|中午|下午|晚上)?(\d{1,2}):(\d{2})', time, text)
    def number(m):
        if locale in ('zh-TW', 'yue') and m[0] == '2' and re.match(r'(?:小時|分鐘|晚|人|個|點)', m.string[m.end():]):
            return '兩'
        return n(m[0])
    text = re.sub(NUMBER, number, text)
    return text
