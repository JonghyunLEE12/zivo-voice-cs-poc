"""Verify a running, already-loaded MLX engine reaps inference on TCP disconnect."""
import argparse
import json
from pathlib import Path
import socket
import time
import urllib.request

import psutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--server-pid', type=int, required=True)
    parser.add_argument('--phase', choices=['before_first_audio', 'after_first_audio'], required=True)
    args = parser.parse_args()
    workers = [p for p in psutil.Process(args.server_pid).children() if 'spawn_main' in ' '.join(p.cmdline())]
    assert len(workers) == 1, 'Warm the engine before running this check'
    worker = workers[0]
    payload = json.dumps({'text': 'Hello, I can help you with your reservation. ' * 8,
                          'locale': 'en', 'stream': True}).encode()
    base = f'http://127.0.0.1:{args.port}'
    if args.phase == 'before_first_audio':
        connection = socket.create_connection(('127.0.0.1', args.port))
        connection.sendall(f'POST /v1/synthesize HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n'.encode() + payload)
        time.sleep(0.5)
        connection.close()
    else:
        request = urllib.request.Request(base + '/v1/synthesize', data=payload, headers={'Content-Type': 'application/json'})
        response = urllib.request.urlopen(request, timeout=60)
        assert response.read(2), 'No first audio received'
        response.close()
    start = time.perf_counter()
    while worker.is_running():
        assert time.perf_counter() - start < 3, 'Inference process was not reaped within 3 seconds'
        time.sleep(0.01)
    result = {'engine': args.engine, 'phase': args.phase, 'worker_pid': worker.pid,
              'reap_ms': (time.perf_counter() - start) * 1000,
              'server_responsive': json.load(urllib.request.urlopen(base + '/v1/info', timeout=5))['engine'] == args.engine}
    target = Path(__file__).parent / 'artifacts' / f'disconnect-{args.engine}.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
