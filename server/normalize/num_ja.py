"""Small Japanese rule set: dates, hours, money, percentages and digits."""
import re

DIGITS = ['ゼロ', 'いち', 'に', 'さん', 'よん', 'ご', 'ろく', 'なな', 'はち', 'きゅう']
DAYS = {1:'ついたち',2:'ふつか',3:'みっか',4:'よっか',5:'いつか',6:'むいか',7:'なのか',8:'ようか',9:'ここのか',10:'とおか',14:'じゅうよっか',20:'はつか',24:'にじゅうよっか',17:'じゅうしちにち',19:'じゅうくにち',27:'にじゅうしちにち',29:'にじゅうくにち'}
MONTHS = {4:'しがつ',7:'しちがつ',9:'くがつ'}
HOURS = {0:'れいじ',4:'よじ',7:'しちじ',9:'くじ'}
NUMBER = r'\d+(?:,\d{3})*(?:\.\d+)?'


def cardinal(value: str | int) -> str:
    s = str(value).replace(',', '')
    if '.' in s:
        a,b = s.split('.', 1)
        return cardinal(a) + 'てん' + ''.join(DIGITS[int(d)] for d in b)
    n = int(s)
    if n == 0:
        return DIGITS[0]
    if n < 0:
        return 'マイナス' + cardinal(-n)
    result = ''
    for unit, label in [(10**12,'ちょう'),(10**8,'おく'),(10**4,'まん')]:
        q,n = divmod(n,unit)
        if q:
            result += ('いっせん' if q == 1000 else cardinal(q)) + label
    for unit, label, special in [(1000,'せん',{3:'さんぜん',8:'はっせん'}),(100,'ひゃく',{3:'さんびゃく',6:'ろっぴゃく',8:'はっぴゃく'}),(10,'じゅう',{})]:
        q,n = divmod(n,unit)
        if q:
            result += special.get(q, ('' if q == 1 else DIGITS[q]) + label)
    return result + (DIGITS[n] if n else '')


def day(n: int) -> str:
    return DAYS.get(n, cardinal(n) + 'にち')


def month(n: int) -> str:
    return MONTHS.get(n, cardinal(n) + 'がつ')


def hour(n: int) -> str:
    if n >= 10 and n % 10 in (4, 7, 9):
        return cardinal(n // 10 * 10) + HOURS[n % 10]
    return HOURS.get(n, cardinal(n) + 'じ')


def minute(n: int) -> str:
    special = {0:'れいふん',1:'いっぷん',3:'さんぷん',4:'よんぷん',6:'ろっぷん',8:'はっぷん',10:'じゅっぷん'}
    if n in special:
        return special[n]
    if n % 10 == 0:
        return cardinal(n)[:-1] + 'っぷん'
    if n > 10:
        return cardinal(n // 10 * 10) + minute(n % 10)
    return cardinal(n) + 'ふん'


def normalize_numbers(text: str) -> str:
    text = re.sub(r'(\d{4})-(\d{2})-(\d{2})', lambda m: cardinal(m[1]) + 'ねん ' + month(int(m[2])) + ' ' + day(int(m[3])), text)
    text = re.sub(r'₩\s*(' + NUMBER + ')', lambda m: cardinal(m[1]) + ' ウォン', text)
    text = re.sub(r'(' + NUMBER + r')\s*ウォン', lambda m: cardinal(m[1]) + ' ウォン', text)
    text = re.sub(r'(' + NUMBER + r')\s*%', lambda m: (cardinal(m[1])[:-1] + 'っ' if cardinal(m[1]).endswith('じゅう') else cardinal(m[1])) + 'パーセント', text)
    text = re.sub(r'(\d{1,2})月(\d{1,2})日', lambda m: month(int(m[1])) + ' ' + day(int(m[2])) + ' ', text)
    text = re.sub(r'(午前|午後)?(\d{1,2}):(\d{2})', lambda m: ('ごぜん ' if m[1]=='午前' else 'ごご ' if m[1] else '') + hour(int(m[2])) + (' はん' if int(m[3])==30 else ' ' + minute(int(m[3])) if int(m[3]) else ''), text)
    text = re.sub(r'(午前|午後)?(\d{1,2})時', lambda m: ('ごぜん ' if m[1]=='午前' else 'ごご ' if m[1] else '') + hour(int(m[2])), text)
    text = re.sub(r'(\d{1,2})分', lambda m: minute(int(m[1])), text)
    text = re.sub(r'(\d{1,2})日', lambda m: day(int(m[1])), text)
    text = re.sub(r'(\d{1,2})月', lambda m: month(int(m[1])), text)
    def counter(m):
        n, unit = int(m[1]), m[2]
        if unit == '人':
            return {1:'ひとり', 2:'ふたり'}.get(n, cardinal(n) + 'にん')
        if unit == '泊':
            return {1:'いっぱく', 3:'さんぱく', 4:'よんぱく', 6:'ろっぱく', 8:'はっぱく', 10:'じゅっぱく'}.get(n, cardinal(n) + 'はく')
        return cardinal(n) + {'名':'めい', '番出口':'ばんでぐち'}[unit]
    text = re.sub(r'(\d+)(人|名|泊|番出口)', counter, text)
    text = re.sub(r'(\d+)ターミナル', lambda m: cardinal(m[1]) + 'ターミナル', text)
    for written, spoken in {'月曜日':'げつようび','火曜日':'かようび','水曜日':'すいようび','木曜日':'もくようび','金曜日':'きんようび','土曜日':'どようび','日曜日':'にちようび'}.items():
        text = text.replace(written, spoken)
    return re.sub(NUMBER, lambda m: cardinal(m[0]), text)
