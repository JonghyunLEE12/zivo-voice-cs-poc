"""Pronunciation metrics independent of model inference. Reference remains display_text."""
import re
import unicodedata
from opencc import OpenCC
from server.normalize.codes import parse_spoken_code
from server.normalize.lexicon import load_lexicon


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j-1] + (x != y)))
        prev = cur
    return prev[-1]


_SMALL = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(), range(20)))
_SMALL.update(dict(zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(), range(20,100,10))))


def _english_number(match):
    words = re.findall(r'[a-z]+', match[0].lower())
    if len(words) > 1 and all(w in _SMALL and _SMALL[w] < 10 for w in words):
        return ''.join(str(_SMALL[w]) for w in words)
    total = current = 0
    for word in words:
        if word in _SMALL:
            current += _SMALL[word]
        elif word == 'hundred':
            current = max(current, 1) * 100
        elif word in ('thousand', 'million'):
            total += max(current, 1) * {'thousand':1000,'million':1000000}[word]
            current = 0
    return str(total + current)


def canonical(text, locale, reference=None):
    text = unicodedata.normalize('NFKC', text).casefold()
    code = parse_spoken_code(text, locale)
    if code:
        # Canonicalize code phrases without replacing unrelated numeric content.
        text = re.sub(r'z\s*v[\s-]*(?:\d[\s-]*){4}', code, text, flags=re.I)
        spoken = re.search(r'(?<![a-z])(?:z\s*v|zee\s+vee|zed\s+vee|ゼット\s*ブイ|ぜっと\s*ぶい)[\s-]*(?:[\w]+[\s-]*){1,6}', text, re.I)
        if spoken:
            # Only consume the shortest prefix that parses as the complete code.
            span = spoken[0]
            for end in range(1, len(span)+1):
                if parse_spoken_code(span[:end], locale) == code:
                    text = text[:spoken.start()] + code + text[spoken.start()+end:]
                    break
    text = re.sub(r'(?<=\d),(?=\d)', '', text)
    if locale == 'en':
        ordinals = dict(zip('first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth'.split(), range(1,21)))
        ordinals.update({'thirtieth':30,'thirty-first':31})
        text = re.sub(r'\b(twenty|thirty)[ -](first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)\b',
                      lambda m:str({'twenty':20,'thirty':30}[m[1]]+ordinals[m[2]]),text)
        text = re.sub(r'\b('+'|'.join(ordinals)+r')\b',lambda m:str(ordinals[m[0]]),text)
        words = '|'.join([*_SMALL, 'hundred','thousand','million'])
        text = re.sub(rf'\b(?:{words})(?:(?:[\s-]+(?:and[\s-]+)?)(?:{words}))*\b', _english_number, text)
        text = text.replace('₩',' won ').replace('%',' percent ')
        # Currency prefix and suffix carry the same meaning.
        text = re.sub(r'won\s+(\d+)', r'\1 won', text)
        text = re.sub(r'(\d+)(?:st|nd|rd|th)\b', r'\1', text)
    elif locale.startswith('zh') or locale == 'yue':
        import cn2an
        text = OpenCC('t2s').convert(text).replace('两','二')
        text = re.sub(r'[零〇一二三四五六七八九十百千万亿]+',
                      lambda m: _chinese_number(m[0], cn2an), text)
        text = re.sub(r'百分之(\d+)', r'\1%', text)
        def clock_number(match):
            prefix,hour,minute=match[1] or '',int(match[2]),match[3]
            if prefix in ('晚上','下午','傍晚') and hour < 12:
                hour += 12
            minute = '30' if minute=='半' else str(int(minute or 0))
            return f'{prefix}{hour}时{minute}分'
        text = re.sub(r'(晚上|下午|傍晚|上午|早上)?(\d{1,2})(?:[:：](\d{2})|[点時时](半|\d{1,2})?分?)',
                      lambda m:clock_number((None,m[1],m[2],m[3] or m[4])),text)
    elif locale == 'ja' and reference:
        # Map only numeric readings derived from display-reference spans; never
        # normalize the whole reference to full (would erase dictionary effects).
        from server.normalize.num_ja import normalize_numbers
        spans = re.findall(r'\d[\d,]*(?:月\d+日|時\d+分|時|月|日|分|%|ウォン)?', reference)
        for span in sorted(spans, key=len, reverse=True):
            reading = normalize_numbers(span)
            if reading != span:
                text = text.replace(reading, span.replace(',','')).replace(reading.replace(' ',''), span.replace(',',''))
        import cn2an
        text = re.sub(r'[0-9〇零一二三四五六七八九十百千万億]+',
                      lambda m:_chinese_number(m[0].replace('億','亿'),cn2an) if any(not c.isascii() for c in m[0]) else m[0],text)
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in text).split())


def _chinese_number(text, module):
    try:
        if all(c in '零〇一二三四五六七八九' for c in text):
            return ''.join(str('零一二三四五六七八九'.index(c.replace('〇','零'))) for c in text)
        return str(module.cn2an(text, 'smart'))
    except ValueError:
        return text


def pronunciation_metrics(reference, hypothesis, locale):
    ref = canonical(reference, locale, reference)
    hyp = canonical(hypothesis, locale, reference)
    units_ref = ref.split() if locale == 'en' else list(ref.replace(' ',''))
    units_hyp = hyp.split() if locale == 'en' else list(hyp.replace(' ',''))
    out = {'metric':'WER' if locale == 'en' else 'CER', 'error_rate':distance(units_ref,units_hyp)/max(1,len(units_ref)),
           'reference_canonical':ref, 'hypothesis_canonical':hyp}
    names = []
    compact = lambda s: canonical(s,locale).replace(' ','')
    for item in load_lexicon():
        aliases = [item['display'][locale], *item.get('aliases', [])]
        if any(compact(a) in compact(reference) for a in aliases):
            names.append({'id':item['id'], 'hit':any(compact(a) in compact(hypothesis) for a in aliases)})
    out['proper_names'] = names
    out['name_hits'] = sum(n['hit'] for n in names)
    out['name_total'] = len(names)
    if locale == 'ja':
        try:
            import pyopenjtalk
            kana = lambda s: re.sub(r'\W','',pyopenjtalk.g2p(s,kana=True))
            a,b = kana(ref),kana(hyp)
            out['kana_cer'] = distance(a,b)/max(1,len(a))
        except Exception as exc:
            out['kana_failure'] = f'pyopenjtalk unavailable: {exc}'
    return out
