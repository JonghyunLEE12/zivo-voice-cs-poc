#!/usr/bin/env python3
"""Render every engine × locale metric cell, including explicit failures."""
import argparse
import json
from pathlib import Path
import statistics
from tts_common import ROOT, PORTS, LOCALES, supported


def percentile(values, fraction):
    values=sorted(values)
    if not values:
        return None
    index=(len(values)-1)*fraction
    lo=int(index);hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(index-lo)


def render(data):
    failure=lambda reason:'실패: '+str(reason).replace('|','/').replace('\n',' ')
    lines=['# TTS benchmark', '',f"Created: {data['created_at']}", '',
           'RTF = synthesis time / generated audio duration. One warmup; three repeats per sentence/normalization unless stated.',
           'Streaming TTFA is first PCM; nonstreaming TTFA waits for the complete sentence. Values below use full normalization; raw/full pronunciation is shown separately.',
           'Peak memory is max(RSS, backend allocation), not their sum. It excludes the HTTP supervisor and may not equal total unified memory.',
           '', '| Engine | Locale | N success/attempt | Load ms | RTF median / p90 | TTFA median ms (mode) | Peak GB | Whisper WER/CER raw → full | ja kana CER raw → full | Name hits raw → full | Failure rate | Qwen3-ASR |',
           '|---|---|---|---|---|---|---|---|---|---|---|---|']
    summary=[]
    for engine in PORTS:
        info=data['engines'].get(engine,{})
        reduced=data.get('sampling_policy',{}).get(engine,{}).get('repeats',3)==1
        for locale in LOCALES:
            rows=[r for r in data['rows'] if r['engine']==engine and r['locale']==locale]
            full=[r for r in rows if r['normalize']=='full' and (not reduced or r['repeat']==0)]
            good=[r for r in full if not r.get('failure') and 'rtf' in r]
            reason='unsupported locale' if not supported(engine,locale) else info.get('failure') or next((r['failure'] for r in full if r.get('failure')), 'not measured')
            missing=failure(reason)
            metric=lambda key,fmt='.3f': format(statistics.median([r[key] for r in good]),fmt) if good else missing
            stream=info.get('supports_streaming', next((r.get('info',{}).get('supports_streaming') for r in good),False))
            peak=max((r.get('info',{}).get('peak_mem_bytes',0) for r in good),default=0)
            def score(level, key):
                values=[r['whisper'][key] for r in rows if r['normalize']==level and (not reduced or r['repeat']==0) and key in r.get('whisper',{})]
                return f'{statistics.mean(values):.3f}' if values else failure('Whisper not scored' if key!='kana_cer' else 'pyopenjtalk unavailable or not scored')
            def names(level):
                values=[r['whisper'] for r in rows if r['normalize']==level and (not reduced or r['repeat']==0) and 'name_total' in r.get('whisper',{})]
                hits=sum(v['name_hits'] for v in values);total=sum(v['name_total'] for v in values)
                return f'{hits}/{total} ({hits/total:.1%})' if total else failure('no scored dictionary targets')
            rtf=metric('rtf')+' / '+(f'{percentile([r["rtf"] for r in good],.9):.3f}' if good else missing)
            ttfa=metric('ttfa_ms','.1f')+f' ({"stream" if stream else "whole sentence"})'
            error=f'{score("raw","error_rate")} → {score("full","error_rate")}' if supported(engine,locale) else missing
            kana=f'{score("raw","kana_cer")} → {score("full","kana_cer")}' if locale=='ja' else '해당 없음'
            name=f'{names("raw")} → {names("full")}' if supported(engine,locale) else missing
            if reduced and supported(engine,locale):
                rtf=metric('rtf')+' / 축소 측정(표본 1): 반복 p90 미산출'
                error='축소 측정(표본 1), full '+score('full','error_rate')+'; raw 비교 제외'
                name='full '+names('full')+'; raw 비교 제외'
                if locale=='ja':
                    kana='full '+score('full','kana_cer')+'; raw 비교 제외'
            fail=f'{sum(bool(r.get("failure")) for r in full)}/{len(full)} ({sum(bool(r.get("failure")) for r in full)/len(full):.1%})' if full else missing
            load=f'{info["load_ms"]:.1f}' if info.get('load_ms') else missing
            cells=[engine,locale,f'{len(good)}/{len(full)}',load,rtf,ttfa,f'{peak/1e9:.3f}' if peak else missing,error,kana,name,fail,failure('optional model not installed')]
            lines.append('| '+' | '.join(cells)+' |')
            summary.append(dict(engine=engine,locale=locale,success=len(good),attempts=len(full),
                                rtf_median=statistics.median([r['rtf'] for r in good]) if good else None,
                                rtf_p90=None if reduced else percentile([r['rtf'] for r in good],.9)))
    lines += ['', '## Interpretation and limits', '',
              '- raw/full denote text normalization. Whisper scores the original engine WAV; blind audio receives separate common loudness/silence processing.',
              '- Whisper is a proxy, not human pronunciation truth. Dictionary prompts bias recognition; speaker identity and naturalness require human listening.',
              '- Numeric inverse normalization is deterministic but incomplete for alternate readings; canonical reference/hypothesis and raw transcripts remain in JSON for audit.',
              '- Silence: RMS <= 1e-5 or peak <= 1e-4. Length heuristics: >3× or <0.25× estimated duration (English 2.5 words/s, other locales 5 characters/s); truncation is a heuristic, not a semantic guarantee.',
              '- 대만 억양 참조 없음. All cloning models share the Mandarin reference for zh-TW; Kokoro uses a different preset speaker and simplified Chinese G2P.',
              '- Qwen3-ASR is optional and not installed. Japanese kana scoring uses pyopenjtalk when available.',
              '- pyopenjtalk may itself misread Korean place-name kanji in the display reference; Japanese kana CER must be interpreted alongside lexicon hits and native listening.',
              '- For engines marked as reduced, full repeat=0 supplies primary statistics; retained repeat>0/raw rows are excluded from that primary comparison. Reused rows retain their original cumulative memory high-watermarks.',
              '- Load time is the engine-reported import/model initialization time; HTTP supervisor startup is excluded by the existing engine contract.',
              '- STT scores use first-repeat WAVs; latency/RTF statistics use all repeats. Scores are sentence-macro averages, proper-name hits are sentence/lexicon-target micro totals.',
              '', '## Run notes', '', *['- '+note for note in data.get('notes',[])],
              '', f"Normalization source SHA256: `{data.get('normalization_sha256','unknown')}`", '']
    return '\n'.join(lines),summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path)
    args=parser.parse_args()
    path=args.input or sorted((ROOT/'reports').glob('bench-????????.json'))[-1]
    text,summary=render(json.loads(path.read_text()))
    path.with_suffix('.md').write_text(text)
    print(path.with_suffix('.md'))
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':main()
