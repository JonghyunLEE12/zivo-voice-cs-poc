from pathlib import Path
import re
import pytest
import yaml
from server.normalize.pipeline import normalize, variants
from server.normalize.lexicon import load_lexicon, initial_prompt
from server.normalize.codes import parse_spoken_code
from server.normalize.num_ja import day, cardinal

@pytest.mark.parametrize('locale,source,expected', [
('en','ZV-4821','Z V four eight two one'),
('en','₩286,000','two hundred eighty-six thousand won'),
('en','November 3rd','November 3rd'),
('en','7:30 PM','seven thirty p m'),
('zh-CN','ZV-7305','Z V 七三零五'),
('zh-CN','78,000韩元','七万八千韩元'),
('zh-CN','11月7日晚上19:30','十一月七日晚上七点半'),
('zh-TW','412,000韓元','四十一萬兩千韓元'),
('ja','ZV-2610','ゼット ブイ に ろく いち ゼロ'),
('ja','11月5日午前10時','じゅういちがつ いつか ごぜん じゅうじ'),
('ja','130,000ウォン','じゅうさんまん ウォン'),
('en','50%','fifty percent'),('zh-CN','50%','百分之五十'),
('zh-TW','50%','百分之五十'),('ja','50%','ごじゅっパーセント'),
])
def test_design_examples(locale,source,expected):
    assert normalize(source,locale,'full') == expected

@pytest.mark.parametrize('locale,source,expected', [
('en','Z V four eight two one','ZV-4821'),
('en','My code is ZV-4821, last name Carter.','ZV-4821'),
('en','zee vee zero zero one two','ZV-0012'),
('zh-CN','Z V 四八二一','ZV-4821'),
('zh-TW','訂單ＺＶ－５１９６','ZV-5196'),
('ja','ゼットブイ よんはちにいち','ZV-4821'),
('ja','ゼット ブイ に ろく いち ゼロ','ZV-2610'),
('en','ZV-48210',None),('en','ZV-482',None),
('en','ZV-4821A',None),('en','AZV-4821',None),
('en','ZV-4821 and ZV-7305',None),('en','My phone is 4821',None),
('en','ZV four eight two onetime',None),
])
def test_spoken_codes(locale,source,expected):
    assert parse_spoken_code(source,locale) == expected

@pytest.mark.parametrize('locale', ['en','zh-CN','zh-TW','ja','yue'])
def test_all_dictionary_items(locale):
    entries = load_lexicon()
    assert len(entries) == 13
    for entry in entries:
        assert normalize(entry['display'][locale],locale,'lex') == entry.get('tts',{}).get(locale,entry['display'][locale])
        assert entry['display'][locale] in initial_prompt(locale)


def test_longest_match_and_ascii_boundary():
    assert normalize('Hanbit Stay Myeongdong','en','lex') == 'Hahn-bit Stay Myung-dong'
    assert normalize('Myeongdong Station','ja','lex') == 'ミョンドンえき'
    assert normalize('AREXtra','en','lex') == 'AREXtra'


def test_fixed_sentences():
    rows = yaml.safe_load(Path('data/blind/sentences.yaml').read_text())
    assert len(rows) == 35
    assert len(set(r['id'] for r in rows)) == 35
    for row in rows:
        result = variants(row['text'],row['locale'])
        assert result['tts_text_raw'] == row['text']
        assert result['tts_text_full']
        # Written English ordinals intentionally remain unchanged.
        if row['locale'] != 'en':
            assert not re.search(r'\d',result['tts_text_full'])


def test_day_readings_and_number_units():
    assert [day(d) for d in (1,4,14,20,24,31)] == ['ついたち','よっか','じゅうよっか','はつか','にじゅうよっか','さんじゅういちにち']
    assert cardinal(38600) == 'さんまんはっせんろっぴゃく'


def test_cleanup_and_invalid_parameters():
    assert normalize('(hello) / world 😀','en') == 'hello world'
    with pytest.raises(ValueError): normalize('a','ko')
    with pytest.raises(ValueError): normalize('a','en','bogus')

@pytest.mark.parametrize('source,expected', [
('20,000韓元','兩萬韓元'),('120,000韓元','十二萬韓元'),
('220,000韓元','二十二萬韓元'),('2,000,000韓元','兩百萬韓元'),
('200韓元','兩百韓元'),
])
def test_taiwan_two_before_units(source,expected):
    assert normalize(source,'zh-TW')==expected


def test_japanese_zero_minutes_and_percent_rounding():
    assert normalize('0分','ja')=='れいふん'
    assert normalize('20分','ja')=='にじゅっぷん'
    assert normalize('100%','ja')=='ひゃくパーセント'
    assert normalize('50.5%','ja')=='ごじゅうてんごパーセント'

@pytest.mark.parametrize('locale', ['zh-TW','yue'])
@pytest.mark.parametrize('source,expected', [
('12個人，22人，2點','十二個人，二十二人，兩點'),
('2個人，2:00，12:00','兩個人，兩點，十二點'),
('2026年11月3日','二零二六年十一月三日'),
])
def test_review_chinese_tokens(locale,source,expected):
    assert normalize(source,locale)==expected

@pytest.mark.parametrize('source,expected', [
('19:30','じゅうくじ はん'),('14時','じゅうよじ'),
('19時','じゅうくじ'),('17時','じゅうしちじ'),
('2人','ふたり'),('1泊','いっぱく'),('2泊','にはく'),
('2名','にめい'),('5番出口','ごばんでぐち'),
('第1ターミナル','第いちターミナル'),('10,000,000','いっせんまん'),
])
def test_review_japanese_readings(source,expected):
    assert normalize(source,'ja')==expected
