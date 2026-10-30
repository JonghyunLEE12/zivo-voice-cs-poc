#!/usr/bin/env python3
"""Local text support agent: single --text request or interactive conversation."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server.llm.ollama_client import VoiceAgent

async def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--locale',choices=['en','zh-CN','zh-TW','ja','yue'],default='en')
    p.add_argument('--text')
    p.add_argument('--events',action='store_true',help='Print JSON events including tool traces and metrics')
    args=p.parse_args()
    agent=VoiceAgent(args.locale)
    while True:
        if args.text is not None: text=args.text
        else:
            try: text=await asyncio.to_thread(input,'you> ')
            except EOFError: break
            if text.strip() in ('/quit','/exit'): break
        async for event in agent.stream_turn(text):
            if args.events: print(json.dumps(event,ensure_ascii=False),flush=True)
            elif event['type']=='llm.delta': print(event['text'],end='',flush=True)
            elif event['type']=='tool.call': print(f"\n[tool: {event['name']}]",file=sys.stderr)
        if not args.events: print()
        if args.text is not None or agent.session.transferred: break

if __name__=='__main__': asyncio.run(main())
