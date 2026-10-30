#!/usr/bin/env python3
"""Run 16 real Ollama text scenarios, retain traces and fail on unmet criteria."""
import argparse
import asyncio
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.llm.ollama_client import VoiceAgent,convert_script,response_metrics
from server.normalize.lexicon import load_lexicon
from server.llm.fake_db import FakeDB
from scripts.llm_resources import ResourceGuard, ResourceLimit
from server.llm.ollama_client import OllamaClient

CASES = {
'en': {
 'lookup':'Please look up my reservation ZV-4821. My last name is Carter.',
 'mismatch':'Please look up my reservation ZV-4821. My last name is Smith.',
 'cancellation':'My reservation is ZV-5196 and my last name is Chen. If I cancel today, what is the cancellation fee and refund?',
 'human':'I want to speak to a human agent, please connect me now.',
},
'zh-CN': {
 'lookup':'请帮我查询预订，预订号ZV-7305，我姓王。',
 'mismatch':'请帮我查询预订，预订号ZV-7305，我姓李。',
 'cancellation':'预订号ZV-7305，我姓王。如果今天取消，要收多少费用，可以退多少钱？',
 'human':'请现在帮我转接人工客服。',
},
'zh-TW': {
 'lookup':'請幫我查詢訂房，訂單編號ZV-5196，我姓陳。',
 'mismatch':'請幫我查詢訂房，訂單編號ZV-5196，我姓李。',
 'cancellation':'訂單編號ZV-5196，我姓陳。如果今天取消，要收多少手續費，可以退多少錢？',
 'human':'請現在幫我轉接真人客服。',
},
'ja': {
 'lookup':'予約を確認してください。予約番号はZV-2610、名字は佐藤です。',
 'mismatch':'予約を確認してください。予約番号はZV-2610、名字は田中です。',
 'cancellation':'予約番号ZV-2610、名字は佐藤です。今日キャンセルした場合、キャンセル料と返金額はいくらですか。',
 'human':'人間の担当者につないでください。',
},
}
EXPECTED = {'en':('ZV-4821',143000,50),'zh-CN':('ZV-7305',78000,0),'zh-TW':('ZV-5196',412000,0),'ja':('ZV-2610',130000,0)}


def validate_quality(result, locale):
    errors = []
    text = result['text']
    raw = result.get('raw_text')
    if raw is None:
        errors.append('raw_text missing: script compliance cannot be verified')
        raw = text
    if locale == 'en' and (re.search(r'[\u3040-\u30ff\u3400-\u9fff가-힣]',text) or not re.search('[A-Za-z]',text)):
        errors.append('not English')
    if locale in ('zh-CN','zh-TW'):
        if not re.search(r'[\u3400-\u9fff]',text) or re.search(r'[\u3040-\u30ff가-힣]',text):
            errors.append('wrong Chinese language')
        if convert_script(raw,locale) != raw:
            errors.append('raw Chinese script/lexicon required conversion')
    if locale == 'ja' and (not re.search(r'[\u3040-\u30ff]',text) or re.search('[가-힣]',text)):
        errors.append('Japanese kana missing or foreign script')
    metrics = response_metrics(raw,text,locale)
    if metrics['length_violation']: errors.append('response length exceeded')
    if metrics['sentence_violation']: errors.append('response must contain 1-2 sentences')
    if result['metrics']['thinking_chars'] != 0: errors.append('thinking emitted despite think:false')
    return errors


def validate_handoff(result, locale, reason=None):
    errors = []
    handoffs = [t for t in result['tools'] if t['name']=='transfer_to_human']
    if not result.get('transferred') or not handoffs or not handoffs[-1]['result'].get('queued'):
        errors.append('handoff not queued')
    if handoffs:
        args = handoffs[-1]['args']
        if (reason and args.get('reason') != reason) or not re.search('[가-힣]',args.get('summary_ko','')):
            errors.append('handoff reason or Korean summary invalid')
    patterns = {
        'en': (r'queue|waiting list',r'(?:3|three) minutes',r'(?:are|have been|already) (?:now )?connected'),
        'zh-CN': (r'排队|队列',r'[3三]\s*分钟',r'已(?:经)?(?:为您)?(?:转接|接通|连接)'),
        'zh-TW': (r'排隊|佇列|隊列',r'[3三]\s*分鐘',r'已(?:經)?(?:為您)?(?:轉接|接通|連接)'),
        'ja': (r'待ち|順番|待機',r'[3三]\s*分',r'接続済み|接続しました|おつなぎしました|繋がりました'),
    }
    queued, wait, connected = patterns[locale]
    if not re.search(queued,result['text'],re.I) or not re.search(wait,result['text'],re.I):
        errors.append('reply missing queue / about 3-minute wait')
    if re.search(connected,result['text'],re.I): errors.append('reply claims already connected')
    return errors


def has_date(text, value, locale):
    from datetime import date
    d = date.fromisoformat(value[:10])
    years=re.findall(r'(?<!\d)(20\d{2})(?=\s*年|[-/]|\s+(?:January|February|March|April|May|June|July|August|September|October|November|December))',text,re.I)
    years += re.findall(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b',text,re.I)
    if any(int(y) != d.year for y in years): return False
    if d.isoformat() in text: return True
    if locale == 'en':
        month = d.strftime('%B')
        return bool(re.search(rf'{month}\s+{d.day}(?:st|nd|rd|th)?\b',text,re.I)) or bool(re.search(rf'{month}\s+\d{{1,2}}(?:st|nd|rd|th)?\s*(?:to|through|–|-)\s*{d.day}(?:st|nd|rd|th)?\b',text,re.I))
    text = re.sub(r'\s+', '', text)
    return bool(re.search(rf'{d.month}月\s*{d.day}日',text)) or bool(
        re.search(rf'{d.month}月.*(?:至|到|から|〜|～|~|–|-)\s*{d.day}日',text))


def validate_lookup_reply(text, booking, locale):
    errors = []
    if booking.get('name') not in text: errors.append('reply omitted/wrong booking name')
    for key in ('check_in','check_out','start_at'):
        if booking.get(key) and not has_date(text,booking[key],locale):
            errors.append(f'reply omitted/wrong {key} date')
    return errors


def validate_mismatch_reply(text):
    names = {name for row in load_lexicon() for name in row['display'].values()}
    if any(name in text for name in names): return ['mismatch leaked localized booking/place name']
    if re.search(r'₩|\d[\d,]*\s*(?:won|KRW|ウォン|韩元|韓元)',text,re.I):
        return ['mismatch leaked money']
    if any(str(row['total_krw']) in text.replace(',','') for row in FakeDB().rows.values()):
        return ['mismatch leaked booking total']
    return []


def validate(result,locale,kind):
    errors = validate_quality(result,locale)
    text, tools = result['text'], result['tools']
    names = [t['name'] for t in tools]
    expected_names = {'lookup':['lookup_reservation'],'mismatch':['lookup_reservation'],
                      'cancellation':['lookup_reservation','get_cancellation_policy'],'human':['transfer_to_human']}[kind]
    if names != expected_names: errors.append(f'tool sequence {names} != {expected_names}')
    code,refund,fee = EXPECTED[locale]
    if kind == 'cancellation' and locale == 'en': code,refund,fee = 'ZV-5196',412000,0
    if kind in ('lookup','cancellation'):
        matches = [t for t in tools if t['name']=='lookup_reservation']
        if not matches or not matches[0]['result'].get('found') or matches[0]['result'].get('reservation_code') != code:
            errors.append('lookup did not verify correct booking')
        elif kind == 'lookup': errors += validate_lookup_reply(text,matches[0]['result'],locale)
    if kind == 'mismatch':
        if not tools or tools[0]['result'].get('found') is not False: errors.append('surname mismatch did not fail')
        if any(t['result'].get('found') for t in tools): errors.append('mismatch leaked a booking')
        errors += validate_mismatch_reply(text)
    if kind == 'cancellation':
        matches = [t for t in tools if t['name']=='get_cancellation_policy']
        if not matches or matches[0]['result'].get('refund_krw') != refund or matches[0]['result'].get('fee_percent') != fee:
            errors.append('wrong policy/refund')
        if not matches or text.strip() != matches[0]['result'].get('spoken_summary'):
            errors.append('reply differs from exact fee/refund spoken_summary')
    if kind == 'human': errors += validate_handoff(result,locale,'user_request')
    return errors


def scenario_prompt(locale, kind, variant):
    base=CASES[locale][kind]
    if variant == 0: return base
    replacements = {
        'en': [('Please look up my reservation','Could you check my booking'),('My last name is','My surname is'),('If I cancel today, what is the cancellation fee and refund?','Please tell me today’s cancellation fee and how much I would get back.'),('I want to speak to a human agent, please connect me now.','Could you put me in touch with a human support agent?')],
        'zh-CN':[('请帮我查询预订','麻烦查一下我的订单'),('如果今天取消，要收多少费用，可以退多少钱？','我想了解今天取消的费用和退款金额。'),('请现在帮我转接人工客服。','我想找人工客服帮忙，可以帮我排队吗？')],
        'zh-TW':[('請幫我查詢訂房','麻煩查一下我的訂單'),('如果今天取消，要收多少手續費，可以退多少錢？','想了解今天取消的手續費與退款金額。'),('請現在幫我轉接真人客服。','我想找真人客服協助，可以幫我排隊嗎？')],
        'ja':[('予約を確認してください。','予約内容を教えていただけますか。'),('今日キャンセルした場合、キャンセル料と返金額はいくらですか。','本日のキャンセル料と返金される金額を教えてください。'),('人間の担当者につないでください。','人間の担当者と話したいので、取り次ぎをお願いします。')],
    }
    for before,after in replacements[locale]: base=base.replace(before,after)
    if variant == 2:
        prefix={'en':'Hello, I need your help. ','zh-CN':'您好，我需要帮助。','zh-TW':'您好，我需要協助。','ja':'すみません、教えてください。'}
        # Reorder information as well as greeting for lookup/cancellation requests.
        if locale == 'en' and kind in ('lookup','mismatch'):
            parts=base.split('. ')
            base='. '.join(reversed(parts))
        base=prefix[locale]+base
    return base


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='reports/llm-scenarios.json')
    parser.add_argument('--locales',default='en,zh-CN,zh-TW,ja')
    parser.add_argument('--variants',type=int,choices=(1,2,3),default=1)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    rows=[]
    path=Path(args.output);path.parent.mkdir(exist_ok=True,parents=True)
    if args.resume and path.exists(): rows=json.loads(path.read_text())
    completed={(r['locale'],r['scenario'],r['variant']) for r in rows}
    guard,client=ResourceGuard(),OllamaClient()
    guard.check(starting=True)
    try:
        warmup=await guard.run(client.warmup)
        path.with_suffix('.warmup.json').write_text(json.dumps(warmup,indent=2)+'\n')
        for variant in range(args.variants):
            for locale in args.locales.split(','):
                for kind in CASES[locale]:
                    if (locale,kind,variant) in completed: continue
                    prompt=scenario_prompt(locale,kind,variant)
                    row={'locale':locale,'scenario':kind,'variant':variant,'prompt':prompt}
                    try:
                        agent=VoiceAgent(locale,client=client)
                        result=await guard.run(lambda:agent.turn(prompt))
                        row.update(result=result,errors=validate(result,locale,kind))
                    except ResourceLimit: raise
                    except Exception as exc:
                        row.update(errors=[f'{type(exc).__name__}: {exc}'])
                    row['passed']=not row['errors'];rows.append(row)
                    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
                    print(f'{locale} {kind} v{variant}: {"PASS" if row["passed"] else "FAIL"} {row["errors"]}',flush=True)
    finally:
        await client.unload()
    passed=sum(r['passed'] for r in rows)
    violations=sum(r.get('result',{}).get('metrics',{}).get('length_violation',False) for r in rows)
    print(f'{passed}/{len(rows)} passed; length violations logged: {violations}',flush=True)
    return int(passed!=len(rows))

if __name__=='__main__': sys.exit(asyncio.run(main()))
