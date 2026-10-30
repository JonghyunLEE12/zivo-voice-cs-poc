#!/usr/bin/env python3
"""Private operator aggregation; public API always uses anonymous labels."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server.blind.store import BlindStore
from server.blind.aggregate import aggregate,csv_export
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--locale');p.add_argument('--native');p.add_argument('--device')
p.add_argument('--format',choices=['json','csv'],default='json')
p.add_argument('--reveal',action='store_true',help='Operator-only actual engine names')
a=p.parse_args();s=BlindStore()
r=aggregate(s.votes(),locale=a.locale,native=a.native,device=a.device,labels=None if a.reveal else s.labels)
print(csv_export(r) if a.format=='csv' else json.dumps(r,ensure_ascii=False,indent=2))
