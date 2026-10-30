#!/usr/bin/env python3
"""Export closed-call metrics from append-only session/turn logs without loading models."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.pipeline.sessions import aggregate

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--log-dir',type=Path,default=ROOT/'data/logs')
    parser.add_argument('--output',type=Path,default=ROOT/'reports/session-metrics.json')
    args=parser.parse_args()
    data=aggregate(args.log_dir)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(args.output),'engines':{k:v['calls'] for k,v in data.items()}},ensure_ascii=False))
