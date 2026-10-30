"""Connection-level call records and latest-revision aggregation; clocks never mix."""
from datetime import datetime
from html import escape
import json
from pathlib import Path
import statistics
from server.config import ROOT
from .latency import now_ms


def append_session(call, closed=False, ended_ms=None):
    if call.started_ms is None:
        return
    path = (Path(call.runtime.log_path).with_name('sessions.jsonl') if call.runtime.log_path else
            ROOT/'data/logs'/f'sessions-{datetime.now():%Y%m%d}.jsonl')
    path.parent.mkdir(parents=True, exist_ok=True)
    call.session_revision += 1
    record = dict(session_id=call.session_id, revision=call.session_revision,
                  timestamp=datetime.now().astimezone().isoformat(), closed=closed,
                  duration_ms=(ended_ms if ended_ms is not None else now_ms())-call.started_ms, engines=sorted(call.engines),
                  handoff_queued=bool(getattr(call.agent.session, 'transferred', False)))
    with path.open('a') as out:
        out.write(json.dumps(record)+'\n')


def latest(paths, keys):
    rows = {}
    for path in sorted(paths):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
                key = tuple(row[k] for k in keys)
                if row.get('revision', 0) >= rows.get(key, {}).get('revision', -1):
                    rows[key] = row
            except (ValueError, KeyError):
                continue  # an interrupted append is not a complete observation
    return list(rows.values())


def distribution(values):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    def p(q):
        if not values: return None
        i=(len(values)-1)*q; lo=int(i); hi=min(lo+1,len(values)-1)
        return values[lo]+(values[hi]-values[lo])*(i-lo)
    return dict(n=len(values), p50=p(.5), p90=p(.9), min=min(values) if values else None,
                max=max(values) if values else None)


def aggregate(log_dir=ROOT/'data/logs'):
    log_dir = Path(log_dir)
    sessions = latest(log_dir.glob('sessions*.jsonl'), ('session_id',))
    turns = latest(log_dir.glob('turns*.jsonl'), ('session_id', 'turn_num'))
    result = {}
    for session in sessions:
        if not session['closed']: continue
        rows = [t for t in turns if t['session_id'] == session['session_id']]
        if not rows or any(t.get('measurement_phase') == 'warmup_discarded' for t in rows): continue
        rows = [t for t in rows if t.get('cancel_reason') != 'merged']
        engine = session['engines'][0] if len(session['engines']) == 1 else 'mixed'
        group = result.setdefault(engine, [])
        group.append({**session, 'turns': len(rows), 'user_chars': sum(t.get('user_chars', 0) for t in rows),
                      'barge_in': any(t.get('barge_in', {}).get('during_playback', t.get('barge_in', {}).get('played_ms', 0) > 0) for t in rows),
                      'sources': sorted({t.get('source', 'unknown') for t in rows}),
                      'first_play_ms': [t['durations']['client_vad_to_play_ms'] for t in rows if t.get('durations', {}).get('client_vad_to_play_ms') is not None],
                      'reply_play_ms': [t['durations']['client_vad_to_reply_play_ms'] for t in rows if t.get('durations', {}).get('client_vad_to_reply_play_ms') is not None]})
    summary = {}
    for engine, rows in result.items():
        summary[engine] = dict(calls=len(rows), calls_over_3min=sum(r['duration_ms'] >= 180000 for r in rows),
            over_3min_rate=sum(r['duration_ms'] >= 180000 for r in rows)/len(rows),
            barge_in_rate=sum(r['barge_in'] for r in rows)/len(rows),
            customer_chars_per_call=statistics.mean(r['user_chars'] for r in rows),
            total_turns=sum(r['turns'] for r in rows), handoff_calls=sum(r['handoff_queued'] for r in rows),
            duration_ms=distribution([r['duration_ms'] for r in rows]),
            first_play_ms=distribution([v for r in rows for v in r['first_play_ms']]),
            reply_play_ms=distribution([v for r in rows for v in r['reply_play_ms']]), sessions=rows)
    return summary


def render(summary):
    text = '<h1>통화 지표</h1><p>PoC 소표본: 통계적 우열을 판단하지 않습니다. 종료된 연결 1개를 1콜로 집계합니다. 시작은 session.ready, 끝은 연결 종료이며 대기·재생 시간을 포함합니다. 설정 변경으로 엔진이 달라진 콜은 mixed로 분리합니다. 워밍업은 제외하고 마이크/WAV/가상 ACK 출처는 세션별로 보존합니다.</p>'
    text += '<p>3분 이상 통화 비율 = 길이 ≥180초 콜 / 전체 콜. 끼어들기 비율 = 재생 중 barge-in이 1회 이상 있는 콜 / 전체 콜. 콜당 고객 발화량 = STT 문자 수 합계(공백·문장부호 포함) / 전체 콜. 병합 전 조각은 턴 수에서 제외합니다. 첫 재생 지연은 c_play_start − c_vad_end(ms), 안내 포함 및 답변 전용을 분리합니다. 과거 세션 시작/종료가 없는 T9/T10 로그로 통화 길이를 추정하지 않습니다.</p>'
    text += '<div class="scroll"><table><tr><th>엔진</th><th>콜 수</th><th>3분 이상</th><th>끼어들기 발생 콜</th><th>고객 문자/콜</th><th>턴 수</th><th>상담원 전환 콜</th><th>통화 길이 p50/p90 (초)</th><th>첫 재생 p50/p90 (ms)</th><th>답변 p50/p90 (ms)</th></tr>'
    def pair(d, divisor=1):
        return '미측정' if d['p50'] is None else f"{d['p50']/divisor:.1f} / {d['p90']/divisor:.1f} (n={d['n']})"
    for engine, data in summary.items():
        cells=[engine, data['calls'], f"{data['calls_over_3min']}/{data['calls']} ({data['over_3min_rate']:.1%})",
               f"{data['barge_in_rate']:.1%}", f"{data['customer_chars_per_call']:.1f}", data['total_turns'],
               data['handoff_calls'], pair(data['duration_ms'],1000), pair(data['first_play_ms']), pair(data['reply_play_ms'])]
        text += '<tr>'+''.join('<td>'+escape(str(c))+'</td>' for c in cells)+'</tr>'
    for engine in ('kokoro', 'qwen3tts', 'voxcpm2', 'cosyvoice3'):
        if engine not in summary:
            text += '<tr><td>'+engine+'</td><td colspan="9">미측정: 종료된 통화 표본 없음</td></tr>'
    text += '</table></div><details><summary>세션별 길이·출처·발화량·전환 여부와 지연 분포 원자료</summary><pre>'+escape(json.dumps(summary, ensure_ascii=False, indent=2))+'</pre></details>'
    return text + ('<p>종료된 측정 통화 없음</p>' if not summary else '')
