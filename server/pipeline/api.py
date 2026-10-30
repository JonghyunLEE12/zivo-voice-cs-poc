"""WebSocket control plane. Input validation happens before any inference."""
import json
import math

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .latency import now_ms
from .turn import Call, Runtime, Settings, HEADER, MAX_SAMPLES


def integer(value, name, low=0, high=2**32-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be an integer in [{low}, {high}]')
    return value


def timestamp(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{name} must be a finite nonnegative timestamp')
    return value


def create_router(runtime=None):
    router = APIRouter()
    runtime = runtime or Runtime()

    @router.websocket('/ws/call')
    async def call_socket(socket: WebSocket):
        await socket.accept()
        call = Call(socket, runtime)
        pending = None
        try:
            while True:
                frame = await socket.receive()
                if frame['type'] == 'websocket.disconnect':
                    break
                try:
                    if frame.get('bytes') is not None:
                        metadata, pending = pending, None
                        if metadata is None:
                            raise ValueError('Send utterance metadata immediately before PCM')
                        data = frame['bytes']
                        if len(data) < HEADER.size:
                            raise ValueError('Missing binary header')
                        kind, reserved, sequence, number = HEADER.unpack_from(data)
                        if (kind, reserved, sequence, number) != (1, 0, 0, metadata['turn_num']):
                            raise ValueError('Invalid microphone frame header')
                        pcm = data[HEADER.size:]
                        if len(pcm) != metadata['samples'] * 2:
                            raise ValueError('PCM length does not match samples')
                        await call.start(pcm, metadata, now_ms())
                        continue
                    text = frame.get('text', '')
                    if len(text) > 16384:
                        raise ValueError('Control frame exceeds 16 KiB')
                    message = json.loads(text)
                    if not isinstance(message, dict):
                        raise ValueError('Control frame must be a JSON object')
                    kind = message.get('type')
                    if pending is not None:
                        pending = None
                        raise ValueError('utterance must be followed immediately by binary PCM')
                    if kind == 'session.start':
                        await call.configure(Settings.parse(message))
                    elif call.settings is None:
                        raise ValueError('Send session.start first and wait for session.ready')
                    elif kind == 'utterance':
                        number = integer(message.get('turn_num'), 'turn_num')
                        if number <= call.last_turn:
                            raise ValueError('turn_num must strictly increase within the connection')
                        integer(message.get('samples'), 'samples', 1, MAX_SAMPLES)
                        timestamp(message.get('t_vad_end'), 't_vad_end')
                        if 'c_speech_end_est' in message:
                            timestamp(message['c_speech_end_est'], 'c_speech_end_est')
                        if message.get('source', 'unknown') not in ('unknown', 'vad', 'wav', 'wav_simulated_ack'):
                            raise ValueError('Unsupported measurement source')
                        if message.get('measurement_phase', 'measured') not in ('measured', 'warmup_discarded'):
                            raise ValueError('Unsupported measurement phase')
                        pending = {k: v for k, v in message.items() if not k.startswith('_')}
                    elif kind in ('barge_in', 'playback.started', 'playback.finished'):
                        number = integer(message.get('turn_num'), 'turn_num')
                        client_ms = timestamp(message.get('t_client'), 't_client')
                        turn = call.turn
                        if turn is None or number != turn.number:
                            continue  # stale acknowledgments never affect the current turn
                        if kind == 'barge_in':
                            timestamp(message.get('played_ms'), 'played_ms')
                            if 'during_playback' in message and type(message['during_playback']) is not bool:
                                raise ValueError('during_playback must be boolean')
                            if turn.finalized or (turn.deciding and message.get('reason') in ('speech', 'next_utterance')):
                                continue
                            turn.log.details['barge_in'] = message
                            await turn.cancel()
                        elif kind == 'playback.started':
                            sequence = integer(message.get('chunk_seq'), 'chunk_seq', 0, 65535)
                            turn.playback_started(sequence, client_ms)
                        else:
                            turn.playback_finished(client_ms)
                    else:
                        raise ValueError('Unknown control message type')
                except (ValueError, TypeError, KeyError) as exc:
                    await call.send({'type': 'error', 'code': 'invalid_message', 'message': str(exc)})
                except Exception as exc:
                    await call.send({'type': 'error', 'code': 'session_failed', 'message': str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await call.close()

    return router


router = create_router()
