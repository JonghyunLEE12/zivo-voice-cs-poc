#!/usr/bin/env python3
"""Exercise /ws/call with a WAV, save PCM output and wire evidence.

Playback acknowledgments simulate an ordered player; this script does not play
speakers and its client timings are transport measurements, not human latency.
"""
import argparse
import asyncio
import json
from pathlib import Path
import struct
import subprocess
import sys
from time import perf_counter
import wave

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.pipeline.turn import HEADER, MAX_SAMPLES


def clock_ms():
    return perf_counter() * 1000


def read_wav(path):
    result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 's16le',
                             '-ac', '1', '-ar', '16000', '-'], capture_output=True, check=True)
    pcm = result.stdout
    if not pcm or len(pcm) // 2 > MAX_SAMPLES:
        raise ValueError('Input must contain 0–60 seconds of audio')
    return pcm


async def run(args):
    pcm = read_wav(args.wav)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    events, audio = [], bytearray()
    cancel_sent = cancelled_ms = first_audio_ms = None
    started_ms = clock_ms()
    first_in_sentence = True
    async with websockets.connect(args.url, max_size=8*1024*1024, open_timeout=10) as ws:
        await ws.send(json.dumps({'type': 'session.start', 'locale': args.locale,
                                 'tts_engine': args.engine, 'normalize': args.normalize,
                                 'stt': 'whisper', 'turn_detect': getattr(args, 'turn_detect', 'vad')}))
        ready = json.loads(await asyncio.wait_for(ws.recv(), args.timeout))
        events.append(ready)
        if ready.get('type') != 'session.ready':
            raise RuntimeError(f'Session failed: {ready}')
        vad_ms = clock_ms()
        await ws.send(json.dumps({'type': 'utterance', 'turn_num': 1, 'samples': len(pcm)//2,
                                 't_vad_end': vad_ms, 'source': 'wav_simulated_ack',
                                 'measurement_phase': getattr(args, 'measurement_phase', 'measured')}))
        await ws.send(HEADER.pack(1, 0, 0, 1) + pcm)
        deadline = perf_counter() + args.timeout
        while perf_counter() < deadline:
            frame = await asyncio.wait_for(ws.recv(), max(.01, deadline-perf_counter()))
            if isinstance(frame, bytes):
                kind, reserved, chunk, turn = HEADER.unpack_from(frame)
                if (kind, reserved, turn) != (2, 0, 1) or len(frame[8:]) % 2:
                    raise RuntimeError('Invalid TTS binary frame')
                if first_audio_ms is None:
                    first_audio_ms = clock_ms()
                audio.extend(frame[8:])
                if first_in_sentence:
                    await ws.send(json.dumps({'type': 'playback.started', 'turn_num': 1,
                                             'chunk_seq': chunk, 't_client': clock_ms()}))
                    first_in_sentence = False
                if args.barge_in and cancel_sent is None:
                    cancel_sent = clock_ms()
                    await ws.send(json.dumps({'type': 'barge_in', 'turn_num': 1,
                                             'played_ms': len(audio)/48, 't_client': cancel_sent, 'during_playback': True}))
                continue
            event = json.loads(frame)
            events.append(event)
            if event['type'] == 'tts.sentence':
                first_in_sentence = True
            if event['type'] == 'error':
                raise RuntimeError(event)
            if event['type'] == 'turn.cancelled':
                cancelled_ms = clock_ms() - cancel_sent if cancel_sent is not None else None
                break
            if event['type'] == 'turn.done':
                if args.barge_in and cancel_sent is not None:
                    continue  # already in flight before barge_in arrived
                await ws.send(json.dumps({'type': 'playback.finished', 'turn_num': 1, 't_client': clock_ms()}))
                break
        else:
            raise TimeoutError('No terminal turn event')
    with wave.open(str(output.with_suffix('.wav')), 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(audio)
    result = {'locale': args.locale, 'engine': args.engine, 'input_wav': str(args.wav),
              'audio_bytes': len(audio), 'audio_seconds': len(audio)/48000,
              'non_silent': any(audio), 'barge_in_ack_ms': cancelled_ms,
              'transport_vad_to_first_audio_ms': first_audio_ms-vad_ms if first_audio_ms else None,
              'wall_ms': clock_ms()-started_ms, 'warm': getattr(args, 'warm', False),
              'measurement_phase': getattr(args, 'measurement_phase', 'measured'), 'playback_mode': 'simulated_ack_no_speaker',
              'events': events}
    output.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'events'}, ensure_ascii=False))
    if not audio or not any(audio):
        raise RuntimeError('No non-silent PCM received')
    if args.barge_in and (cancelled_ms is None or cancelled_ms >= 200):
        raise RuntimeError(f'Barge-in 200 ms acceptance failed: {cancelled_ms}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav', type=Path)
    parser.add_argument('--url', default='ws://127.0.0.1:8000/ws/call')
    parser.add_argument('--locale', choices=['en', 'zh-CN', 'zh-TW', 'ja', 'yue'], required=True)
    parser.add_argument('--engine', default='kokoro', choices=['kokoro', 'voxcpm2', 'cosyvoice3', 'qwen3tts'])
    parser.add_argument('--normalize', default='full', choices=['raw', 'lex', 'full'])
    parser.add_argument('--turn-detect', choices=['vad', 'vad+smartturn'], default='vad')
    parser.add_argument('--barge-in', action='store_true')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--output', default='reports/t9/ws-result', help='Output stem, .wav and .json are written')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
