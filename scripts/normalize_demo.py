#!/usr/bin/env python3
"""Print DESIGN §6.1 raw/lex/full comparison for all 35 fixed sentences."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yaml
from server.normalize.pipeline import variants


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / 'data/blind/sentences.yaml'
    rows = [{**row, **variants(row['text'], row['locale'])} for row in yaml.safe_load(path.read_text())]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print('| ID | raw | lex | full |\n|---|---|---|---|')
        for row in rows:
            print('| ' + ' | '.join(str(row[key]).replace('|', '\\|') for key in ['id','tts_text_raw','tts_text_lex','tts_text_full']) + ' |')


if __name__ == '__main__':
    main()
