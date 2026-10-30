#!/usr/bin/env python3
"""Controlled before/after agent comparison with shared revised prompt and 8K context."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.llm.ollama_client import VoiceAgent, OllamaClient
from server.llm.sentences import split_sentences
from scripts.test_llm_scenarios import CASES, validate
from scripts.llm_resources import ResourceGuard

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('server.llm._before',ROOT/'reports/t6_1/before_ollama_client.py')
before=importlib.util.module_from_spec(spec)
spec.loader.exec_module(before)
# Keep the captured implementation intact; dependencies/prompt are shared to isolate
# agent round routing and delivery. Original historical metrics remain in T2-T6 report.
before.__file__=str(ROOT/'server/llm/ollama_client.py')


class MeasuredClient(OllamaClient):
    def __init__(self):
        super().__init__()
        self.rounds=[]
    async def stream_chat(self,messages,tools):
        started=perf_counter(); first=None; done={}
        async for chunk in super().stream_chat(messages,tools):
            if chunk.get('message',{}).get('content') and first is None: first=(perf_counter()-started)*1000
            if chunk.get('done'):
                done={k:v for k,v in chunk.items() if k.endswith('_duration') or k.endswith('_count') or k=='done_reason'}
            yield chunk
        self.rounds.append({'elapsed_ms':(perf_counter()-started)*1000,'round_first_token_ms':first,**done})


async def measure(agent,prompt):
    started=perf_counter(); delivered=''; first_sentence=None; result=None
    async for event in agent.stream_turn(prompt):
        if event['type']=='llm.delta':
            delivered+=event['text']
            sentences=split_sentences(delivered)
            if first_sentence is None and sentences and sentences[0].rstrip().endswith(('.', '!', '?', '。','！','？')):
                first_sentence=(perf_counter()-started)*1000
        if event['type']=='llm.done': result=event
    return {'first_token_ms':result['metrics']['first_token_ms'],
            'first_sentence_ms':first_sentence if first_sentence is not None else (perf_counter()-started)*1000,
            'elapsed_ms':result['metrics']['elapsed_ms'],'text':result['text'],'rounds':agent.client.rounds,
            'tool_sources':[t.get('source','model') for t in result['tools']], 'result':result}


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',default='reports/t6_1/latency.json')
    args=parser.parse_args()
    path=Path(args.output);rows=[];guard=ResourceGuard();control=OllamaClient()
    guard.check(starting=True)
    try:
        warm=await guard.run(control.warmup)
        path.with_suffix('.warmup.json').write_text(json.dumps(warm,indent=2)+'\n')
        for repeat in range(args.repeats):
            for locale in CASES:
                for kind in ('lookup','cancellation'):
                    # Alternate order to reduce systematic prompt-cache advantage.
                    variants=('before','after') if repeat%2==0 else ('after','before')
                    for version in variants:
                        client=MeasuredClient()
                        cls=before.VoiceAgent if version=='before' else VoiceAgent
                        agent=cls(locale,client=client,log_path=ROOT/'reports/t6_1/bench-turns.jsonl')
                        prompt=CASES[locale][kind]
                        row={'version':version,'repeat':repeat,'locale':locale,'scenario':kind,'prompt':prompt}
                        row.update(await guard.run(lambda:measure(agent,prompt)))
                        row['validation_errors']=validate(row['result'],locale,kind)
                        rows.append(row);path.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
                        print(f'{version} {locale} {kind} {repeat}: token={row["first_token_ms"]:.1f} sentence={row["first_sentence_ms"]:.1f} rounds={len(row["rounds"])}',flush=True)
    finally:
        await control.unload()

if __name__=='__main__': asyncio.run(main())
