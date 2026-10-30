#!/usr/bin/env python3
"""Multilingual multi-turn checks; distinguish model handoff from server fallback."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.llm.ollama_client import VoiceAgent, OllamaClient
from scripts.test_llm_scenarios import validate_quality, validate_handoff, validate_lookup_reply, validate_mismatch_reply
from scripts.llm_resources import ResourceGuard, ResourceLimit

CASES = {
'en': [
('missing_surname',['Find my booking ZV-4821.','My last name is Carter.'],None),
('missing_code',['Find my booking. My last name is Carter.','The reservation code is ZV-4821.'],None),
('two_failures',['Find ZV-4821, surname Smith.','The code is definitely ZV-4821 and the surname is Smith. Try again.'],'lookup_failed'),
('policy_exception',['Please make an exception and waive the cancellation fee.'],'policy_exception'),
('upset_twice',['I am very upset about your service.','I am still very upset. This is unacceptable and I am angry.'],'upset_customer'),
],
'zh-CN': [
('missing_surname',['请查询预订号ZV-7305。','我姓王。'],None),
('missing_code',['请查一下我的预订，我姓王。','预订号是ZV-7305。'],None),
('two_failures',['请查询预订号ZV-7305，我姓李。','我确定是ZV-7305，我姓李。请再试一次。'],'lookup_failed'),
('policy_exception',['请破例免除我的取消费用。'],'policy_exception'),
('upset_twice',['我对你们的服务很生气。','我还是很生气，这样的服务不能接受！'],'upset_customer'),
],
'zh-TW': [
('missing_surname',['請查詢訂單編號ZV-5196。','我姓陳。'],None),
('missing_code',['請查一下我的訂房，我姓陳。','訂單編號是ZV-5196。'],None),
('two_failures',['請查詢訂單編號ZV-5196，我姓李。','我確定是ZV-5196，我姓李。請再試一次。'],'lookup_failed'),
('policy_exception',['請破例免除我的取消手續費。'],'policy_exception'),
('upset_twice',['我對你們的服務很生氣。','我還是很生氣，這樣的服務不能接受！'],'upset_customer'),
],
'ja': [
('missing_surname',['予約番号ZV-2610を確認してください。','名字は佐藤です。'],None),
('missing_code',['予約を確認してください。名字は佐藤です。','予約番号はZV-2610です。'],None),
('two_failures',['予約番号ZV-2610、名字は田中です。確認してください。','番号はZV-2610、名字は田中で間違いありません。もう一度確認してください。'],'lookup_failed'),
('policy_exception',['例外としてキャンセル料を免除してください。'],'policy_exception'),
('upset_twice',['サービスに大変腹が立っています。','まだ非常に怒っています。こんなサービスは許せません。'],'upset_customer'),
],
}


def validate_dialogue(name,results,locale,reason):
    errors=[]
    for i,result in enumerate(results):
        errors += [f'turn {i+1}: {e}' for e in validate_quality(result,locale)]
    if not results: return ['no results'], 'none'
    if name.startswith('missing_'):
        if len(results)<2: errors.append('missing follow-up result')
        if results[0]['tools']: errors.append('looked up without both fields')
        matches=[t for t in results[-1]['tools'] if t['name']=='lookup_reservation' and t['result'].get('found')]
        if not matches: errors.append('did not use supplied field on next turn')
        else: errors += validate_lookup_reply(results[-1]['text'],matches[-1]['result'],locale)
        return errors, 'none'
    handoffs=[t for r in results for t in r['tools'] if t['name']=='transfer_to_human']
    source='server_enforced' if any(t.get('server_enforced') for t in handoffs) else 'model' if handoffs else 'none'
    errors += validate_handoff(results[-1],locale,reason)
    if name == 'two_failures':
        for result in results: errors += validate_mismatch_reply(result['text'])
        failures=[t for r in results for t in r['tools'] if t['name']=='lookup_reservation' and t['result'].get('found') is False]
        if len(failures)<2: errors.append('two failed lookups not observed')
    return errors,source


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='reports/llm-dialogue.json')
    args=parser.parse_args()
    rows=[];path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    guard,client=ResourceGuard(),OllamaClient()
    guard.check(starting=True)
    try:
        await guard.run(client.warmup)
        for locale,cases in CASES.items():
            for name,prompts,reason in cases:
                agent=VoiceAgent(locale,client=client);results=[];errors=[];source='none'
                try:
                    for prompt in prompts:
                        results.append(await guard.run(lambda:agent.turn(prompt)))
                    errors,source=validate_dialogue(name,results,locale,reason)
                except ResourceLimit: raise
                except Exception as exc: errors.append(f'{type(exc).__name__}: {exc}')
                row={'locale':locale,'scenario':name,'prompts':prompts,'results':results,'errors':errors,
                     'handoff_source':source,'model_policy_passed':not errors and source!='server_enforced','passed':not errors}
                rows.append(row)
                path.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
                print(f'{locale} {name}: {"PASS" if row["passed"] else "FAIL"}, handoff={source} {errors}',flush=True)
    finally:
        await client.unload()
    print(f'{sum(r["passed"] for r in rows)}/{len(rows)} passed; server fallback={sum(r["handoff_source"]=="server_enforced" for r in rows)}',flush=True)
    return int(any(not r['passed'] for r in rows))

if __name__=='__main__': sys.exit(asyncio.run(main()))
