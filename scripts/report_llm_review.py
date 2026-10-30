#!/usr/bin/env python3
"""Revalidate retained responses and regenerate the T6.1 measured report section."""
import json
from pathlib import Path
from statistics import median
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.test_llm_scenarios import validate
from scripts.test_llm_dialogue import validate_dialogue

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/t6_1'
locales=['en','zh-CN','zh-TW','ja']

def read(name): return json.loads((OUT/name).read_text())
def save(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def fmt(n): return f'{n:,.1f}'

scenarios=read('scenarios-final.json')
for r in scenarios:
    r['errors']=validate(r['result'],r['locale'],r['scenario']);r['passed']=not r['errors']
save('scenarios-final.json',scenarios)
dialogue=read('dialogue.json')
reasons={'two_failures':'lookup_failed','policy_exception':'policy_exception','upset_twice':'upset_customer'}
for r in dialogue:
    r['errors'],r['handoff_source']=validate_dialogue(r['scenario'],r['results'],r['locale'],reasons.get(r['scenario']))
    r['passed']=not r['errors'];r['model_policy_passed']=r['passed'] and r['handoff_source']!='server_enforced'
save('dialogue.json',dialogue)
latency=read('latency.json')
for r in latency:r['validation_errors']=validate(r['result'],r['locale'],r['scenario'])
save('latency.json',latency)
base=[r for r in scenarios if r['variant']==0]
passed=lambda rows:sum(r['passed'] for r in rows)
pytest=(OUT/'pytest.log').read_text().strip().splitlines()[-1]
lines=[
'## 실행과 결과','',
f'**기존+신규 pytest 통과, 기본 시나리오 {passed(base)}/16, 변형 포함 {passed(scenarios)}/48, 대화 {passed(dialogue)}/20.** 지정된 구조 수정과 검증 재실행을 완료했지만, 모델의 도구 호출 누락이 남아 T9의 정상 상담 품질을 통과했다고 볼 수 없다.','',
'| 완료 기준 | 실제 실행 명령 (프로젝트 루트) | 실제 결과 |',
'|---|---|---|',
f'| pytest | `.venv/bin/python -m pytest tests -q --basetemp reports/t6_1/pytest-temp` | {pytest}; exit 0 |',
'| 정규화 | `.venv/bin/python scripts/normalize_demo.py` | 35문장 raw/lex/full; 설계 5.2 15예시는 pytest에서 통과 |',
f'| 4언어 × 4 시나리오 및 3발화 변형 | `.venv/bin/python scripts/test_llm_scenarios.py --variants 3 --output reports/t6_1/scenarios-final.json` | 기본 {passed(base)}/16, 전체 {passed(scenarios)}/48; exit 1 |',
f'| 4언어 × 5대화 | `.venv/bin/python scripts/test_llm_dialogue.py --output reports/t6_1/dialogue.json` | {passed(dialogue)}/20; exit 1 |',
'| 전후지연 | `.venv/bin/python scripts/bench_llm_review.py --repeats 3` | 4언어 × 조회/취소 × 전후 × 3회 = 48건, exit 0; 품질은 별도 검사 |',
'| 보존 응답 재검사·보고서 | `.venv/bin/python scripts/report_llm_review.py` | 동일 모델 응답을 현재 검사기로 재검사; 아래 수치 재생성 |','',
'최초 최종 실행 로그는 36/48이었다. `2026 年 11 月 10 日`처럼 날짜에 공백이 있는 정상 응답 3건을 검사기가 거절한 오탐을 수정한 뒤 **같은 응답**을 재검사해 39/48이 됐다. 원 판정은 `scenarios-final-original-validation.json`에 보존했다. 모델을 재시도해 좋은 응답만 선택한 결과가 아니다. 이후 명시적 잘못된 연도 거절 검사도 추가했으며 수치는 동일하다.','',
'### 기본 16개 및 변형','',
'| 언어 | 조회 | 성 불일치 | 취소 | 상담원 | 기본 합계 | 3변형 합계 |',
'|---|---|---|---|---|---|---|']
for locale in locales:
    subset=[r for r in base if r['locale']==locale]
    cells=['PASS' if next(r for r in subset if r['scenario']==k)['passed'] else 'FAIL' for k in ['lookup','mismatch','cancellation','human']]
    lines.append('| '+ ' | '.join([locale,*cells,f'{passed(subset)}/4',f'{passed([r for r in scenarios if r["locale"]==locale])}/12'])+' |')
lines += ['', '상담원 FAIL 9건은 zh-CN/zh-TW/ja 각각 3발화에서 `transfer_to_human`을 호출하지 않고 대기열 문장만 출력한 경우다. zh-TW 3건은 raw의 `隊列`이 OpenCC 대만 어휘 `佇列`로 변환되어 원문 준수 검사도 실패했다. 이 검사는 간체 혼입뿐 아니라 설정한 OpenCC 대만 어휘 변경도 엄격히 실패로 센다. 서버의 실제 `transferred`는 false이므로 T9에서 연결 완료/대기열 상태로 간주하면 안 된다.', '',
f'최종 시나리오 48건의 길이 위반 {sum(r["result"]["metrics"]["length_violation"] for r in scenarios)}건, thinking 출력 {sum(r["result"]["metrics"]["thinking_chars"] for r in scenarios)}자. 취소 영어 케이스는 ZV-5196으로 바꿔 수수료0/환불412,000의 비대칭 값을 사용한다. 전환/조회 모델 오류를 숨기는 결정론적 응답 덮어쓰기는 추가하지 않았다.', '',
'### 대화 20개','',
'| 언어 | 성 누락 후 보충 | 코드 누락 후 보충 | 조회 2회 실패 | 정책 예외 | 반복 불만 |',
'|---|---|---|---|---|---|']
for locale in locales:
    subset=[r for r in dialogue if r['locale']==locale]
    cells=[]
    for kind in ['missing_surname','missing_code','two_failures','policy_exception','upset_twice']:
        r=next(r for r in subset if r['scenario']==kind)
        cells.append(('PASS' if r['passed'] else 'FAIL')+(' (서버 강제)' if r['handoff_source']=='server_enforced' else ''))
    lines.append('| '+' | '.join([locale,*cells])+' |')
lines += ['',
f'서버 강제 전환 {sum(r["handoff_source"]=="server_enforced" for r in dialogue)}건(en/zh-CN/zh-TW), 모델 자체 전환 {sum(r["handoff_source"]=="model" for r in dialogue)}건. zh-TW 강제 전환은 실제 큐 등록은 됐으나 raw 대만 어휘 검사 때문에 전체 FAIL이다. 일본어 두 번째 조회 실패에서는 모델이 재조회 자체를 생략해 서버 강제 조건에 도달하지 않았다. 성공과 실패의 전체 발화·도구·원인은 `dialogue.json`에 있다.', '',
'### 첫 토큰 / 첫 문장 전후 비교','',
'단위 ms, 각 칸 3회 중앙값. 워밍업 후 요청 시작 기준이며 로드 시간은 제외한다. **수정 전 구현 snapshot과 수정 후 구현에 같은 최종 프롬프트·현재 도구/DB·8192 context를 적용한 통제 비교**다. 과거 T6 전체 환경을 그대로 재현한 값이 아니다. 반복마다 전후 실행 순서를 번갈아 캐시 순서 편향을 줄였다. 이전 구현의 첫 문장은 llm.delta를 누적해 첫 종결부호가 도착한 시각, 이후는 실제 문장 이벤트 도착 시각이다.', '',
'| 언어·질의 | 전 첫 토큰 | 후 첫 토큰 | 전 첫 문장 | 후 첫 문장 | 전→후 LLM 라운드(중앙값) | 유효 전/후 표본 |',
'|---|---:|---:|---:|---:|---:|---|']
for locale in locales:
    for kind in ['lookup','cancellation']:
        a=[r for r in latency if r['locale']==locale and r['scenario']==kind and r['version']=='before']
        b=[r for r in latency if r['locale']==locale and r['scenario']==kind and r['version']=='after']
        nums=[fmt(median(r[key] for r in sample)) for key,sample in [('first_token_ms',a),('first_token_ms',b),('first_sentence_ms',a),('first_sentence_ms',b)]]
        valid=f'{sum(not r["validation_errors"] for r in a)}/3 → {sum(not r["validation_errors"] for r in b)}/3'
        lines.append('| '+' | '.join([locale+' '+kind,*nums,f'{median(len(r["rounds"]) for r in a):g}→{median(len(r["rounds"]) for r in b):g}',valid])+' |')
lines += ['',
'**영어 조회·취소만 전후 모두 품질 검사 3/3을 통과해 성능 비교가 유효하다.** 영어 조회 첫 토큰은 3,092.3→1,578.6ms, 첫 문장은 4,164.9→2,594.9ms; 취소 첫 토큰은 4,077.6→1,904.3ms, 첫 문장은 4,628.5→2,517.1ms다. 나머지 수정 전 표본은 도구 누락/잘못된 정책/사전 발화로 인한 길이 위반 등이 있어 짧은 응답을 성능 성공으로 보지 않는다. 원문과 검사 사유는 `latency.json`에 모두 보존했다.', '',
'수정 후 측정 24/24는 품질 검사 통과, 전부 1라운드다. 전체 수정 후 첫 토큰 중앙값은 '+fmt(median(r['first_token_ms'] for r in latency if r['version']=='after'))+'ms, 첫 문장은 '+fmt(median(r['first_sentence_ms'] for r in latency if r['version']=='after'))+'ms다. 여전히 첫 토큰 1초 목표를 초과한다. 9b 선택의 판단 자료로만 제공하며 다운로드하지 않았다.', '',
'기존 `reports/T2-T6-REPORT.md`의 첫 토큰 중앙값은 en 3495.4 / zh-CN 3982.4 / zh-TW 3790.0 / ja 3661.9ms였다. 당시 첫 문장 시각은 저장되지 않아 그 값과의 완전한 전후 비교는 불가능하다. 과거 수치를 첫 문장 수치로 대체하지 않았다.', '',
'라운드별 `load_duration`, `prompt_eval_count/duration`, `eval_count/duration`, `total_duration`은 Ollama 원래 단위(ns/count)로 저장하며, `elapsed_ms`는 별도 wall-clock이다. 빈 messages 워밍업 응답은 `done_reason=load`만 주고 duration을 반환하지 않아 워밍업 자체의 지연값은 주장하지 않는다.', '',
'## 남은 실패와 판단','',
'요청된 코드 수정·pytest·실제 검증·보고서 완료 기준은 수행했다. 다만 최종 모델의 상담원 도구 호출 및 일부 다회차 도구 선택은 실패하므로 T9 품질 준비 완료 판정은 보류해야 한다. 추가적인 서버 intent 라우팅/전환 강제나 다른 모델 검증은 이번 지정된 코드 리뷰 범위를 넘어 별도 결정이 필요하다. 테스트를 느슨하게 하거나 실패 응답을 바꿔 PASS로 만들지 않았다.', '']
report=ROOT/'reports/T6.1-REPORT.md';text=report.read_text();start=text.index('## 실행과 결과');end=text.index('## 설계·메모리 제약',start)
report.write_text(text[:start]+'\n'.join(lines)+'\n'+text[end:])
print(f'scenarios {passed(base)}/16, variants {passed(scenarios)}/48, dialogue {passed(dialogue)}/20; '+pytest)
