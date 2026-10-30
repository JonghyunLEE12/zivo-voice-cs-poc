import type { MicVAD } from '@ricky0123/vad-web';
import { microphoneFrame, readAudio } from '../shared/ws';
import { PlaybackQueue } from './playback';
import './style.css';

const dev = new URLSearchParams(location.search).has('dev');
const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;
$('app').innerHTML = `
<header><div class="topline"><span class="eyebrow">ZIVO / VOICE LAB</span><nav><a href="/blind">블라인드 비교</a><a href="/report">측정 리포트</a></nav></div>
<h1>말로 연결되는 여행 상담</h1><p>언어를 고르고 마이크를 켜세요. 답변 중에도 말씀하시면 즉시 멈추고 듣습니다.</p><span class="badge">로컬 음성 상담 PoC · 가상 예약 데이터</span></header>
<div class="layout"><section aria-label="통화 설정"><div class="controls">
<label>상담 언어<select id="locale"><option value="en">English</option><option value="zh-CN">简体中文 · 중국 본토</option><option value="zh-TW">繁體中文 · 대만</option><option value="ja">日本語</option><option value="yue" disabled>粵語 · 지원 엔진 없음</option></select></label>
<label>음성 엔진<select id="engine"><option value="kokoro">Kokoro · 실시간 기본</option><option value="qwen3tts">Qwen3-TTS</option><option value="voxcpm2">VoxCPM2 · 느림(체험용)</option><option value="cosyvoice3">CosyVoice3 · 느림(체험용)</option></select></label>
<p id="engine-hint" class="hint wide">Kokoro는 프리셋 음성을 사용합니다.</p>
<label>발음 정규화<select id="normalize"><option value="full">full · 이름과 숫자 정리</option><option value="raw">raw · 원문 그대로</option><option value="lex">lex · 이름만 정리</option></select></label>
<label>턴 결정<select id="turn-detect"><option value="vad">VAD만</option><option value="vad+smartturn">VAD + Smart Turn v3.2</option></select></label>
<label>발화 종료 무음 <output id="silence-value">500 ms</output><input id="silence" type="range" min="200" max="1500" step="50" value="500"></label>
<label class="inline wide"><input id="echo" type="checkbox" checked>답변 중 감지 임계값 높이기</label>
<p class="hint wide">에코로 인한 끊김을 줄이려면 헤드폰을 사용하세요. 대만 억양 참조 음성은 없습니다.</p>
<button id="apply" class="secondary wide" disabled>설정 적용</button></div></section>
<section aria-label="상담"><div class="actions"><span id="status" role="status">통화 준비</span><div><button id="start">마이크로 시작</button> <button id="stop" class="danger" disabled>통화 종료</button></div></div><p id="error" role="alert"></p>
<div class="hud"><div class="metric"><span>첫 음성까지</span><strong id="latency">—</strong></div><div class="metric"><span>답변 음성까지</span><strong id="reply-latency">—</strong></div><div class="metric"><span>말 끊기 횟수</span><strong id="interruptions">0</strong></div></div>
<p id="server-metrics" class="metrics-detail">클라이언트와 서버의 시계는 각각 따로 계산합니다.</p>
<div id="conversation" class="conversation" aria-live="polite"><p class="empty">예약 조회, 취소 정책, 상담원 연결을 물어보세요.</p></div>
<details><summary>클라이언트 측정 로그</summary><button id="download" class="secondary">JSON 다운로드</button><pre id="logs"></pre></details>
<div id="dev" ${dev ? '' : 'hidden'}><hr><p class="hint">개발용 WAV 입력 · 마이크/VAD를 우회합니다. 서버 모델 모드는 바꾸지 않습니다.</p><button id="connect" class="secondary">WAV 모드 연결</button><input id="wav" type="file" accept="audio/wav,.wav" disabled><button id="interrupt" class="secondary" disabled>말 끊기 테스트</button></div></section></div>
<footer>STT: Whisper · 발화 단위 전송 · Silero VAD · 16 kHz 입력 / 24 kHz 출력<br>실제 예약 변경이나 취소를 실행하지 않는 데모입니다.</footer>`;

type Turn = { id: number; vadEnd: number; speechEnd: number; firstPlay?: number; replyPlay?: number; sentenceRole: Map<number, string>; user: HTMLElement; assistant: HTMLElement; done: boolean; cancelled: boolean; finished: boolean; handoff: boolean; deciding: boolean };
const events: Record<string, unknown>[] = [];
let socket: WebSocket | undefined, context: AudioContext | undefined, queue: PlaybackQueue | undefined, vad: MicVAD | undefined;
let activeDetector = 'vad';
let ready = false, connecting = false, micMode = false, current = 0, bargeCount = 0, epoch = 0;
const turns = new Map<number, Turn>();
function log(type: string, fields: Record<string, unknown> = {}) {
  const event = { type, t_client: performance.now(), ...fields };
  events.push(event); if (events.length > 2000) events.shift();
  $('logs').textContent = events.slice(-14).map(e => JSON.stringify(e)).join('\n');
  console.info('[call]', JSON.stringify(event));
}
function send(data: Record<string, unknown>) { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(data)); }
function status(message: string) { $('status').textContent = message; }
function fail(error: unknown) { $('error').textContent = error instanceof Error ? error.message : String(error); log('error', { message: $('error').textContent }); }
function controls() {
  ($('start') as HTMLButtonElement).disabled = connecting || !!socket;
  ($('connect') as HTMLButtonElement).disabled = connecting || !!socket;
  ($('stop') as HTMLButtonElement).disabled = !socket && !connecting;
  ($('apply') as HTMLButtonElement).disabled = !ready;
  ($('wav') as HTMLInputElement).disabled = !ready || micMode;
  ($('interrupt') as HTMLButtonElement).disabled = !ready;
}
function thresholds() {
  const high = !!queue?.active && ($('echo') as HTMLInputElement).checked;
  vad?.setOptions({ positiveSpeechThreshold: high ? 0.65 : 0.3, negativeSpeechThreshold: high ? 0.5 : 0.25, redemptionMs: Number(($('silence') as HTMLInputElement).value) });
}
function settings() {
  return { type: 'session.start', locale: ($('locale') as HTMLSelectElement).value, tts_engine: ($('engine') as HTMLSelectElement).value, normalize: ($('normalize') as HTMLSelectElement).value, stt: 'whisper', turn_detect: ($('turn-detect') as HTMLSelectElement).value };
}
async function prepareAudio() {
  context ??= new AudioContext({ latencyHint: 'interactive' });
  await context.resume();
  queue ??= new PlaybackQueue(context, (turnNum, seq, time) => {
    const turn = turns.get(turnNum);
    if (!turn || turn.cancelled) return;
    send({ type: 'playback.started', turn_num: turnNum, chunk_seq: seq, t_client: time });
    if (turn.firstPlay === undefined) {
      turn.firstPlay = time; $('latency').textContent = `${Math.round(time - turn.vadEnd)} ms`;
      log('c_play_start', { turn_num: turnNum, chunk_seq: seq, c_play_start: time, c_vad_end: turn.vadEnd, client_vad_to_play_ms: time - turn.vadEnd });
    }
    const firstSeq = [...turn.sentenceRole.keys()].filter(key => key <= seq).sort((a, b) => b - a)[0];
    if (turn.replyPlay === undefined && turn.sentenceRole.get(firstSeq) === 'reply') {
      turn.replyPlay = time; $('reply-latency').textContent = `${Math.round(time - turn.vadEnd)} ms`;
      log('c_reply_play_start', { turn_num: turnNum, t_client: time });
    }
    status('답변 중 · 말씀하시면 멈춥니다'); thresholds();
  }, turnNum => {
    const turn = turns.get(turnNum);
    if (!turn || turn.cancelled) return;
    turn.finished = true;
    send({ type: 'playback.finished', turn_num: turnNum, t_client: performance.now() });
    log('playback.finished', { turn_num: turnNum }); thresholds();
    if (turn.handoff) { status('상담원 연결 중'); ready = false; void vad?.pause(); controls(); }
    else status(micMode ? '듣고 있어요' : 'WAV 입력 대기');
  });
}
function interrupt(reason = 'speech') {
  const turn = turns.get(current);
  if (!turn || turn.cancelled || turn.finished) return;
  if (turn.deciding && (reason === 'speech' || reason === 'next_utterance')) return;
  const started = performance.now();
  const nodesActive = queue?.active || false;
  const played = queue?.stop() || 0;
  const stopped = performance.now();
  turn.cancelled = true;
  send({ type: 'barge_in', turn_num: current, played_ms: played, t_client: started, reason, during_playback: nodesActive && ['speech', 'test', 'next_utterance'].includes(reason) });
  if (nodesActive && (reason === 'speech' || reason === 'test')) { bargeCount++; $('interruptions').textContent = String(bargeCount); }
  log('barge_in', { turn_num: current, reason, played_ms: played, nodes_active_before: nodesActive, nodes_active_after: queue?.active, stop_ms: stopped - started });
  thresholds(); status('재생 중단 · 듣고 있어요');
}
function utterance(audio: Float32Array, source: 'vad' | 'wav', vadEnd = performance.now()) {
  if (!ready || !queue || !socket) return;
  if (!audio.length || audio.length > 960000) throw new Error('발화는 0초 초과, 60초 이하여야 합니다.');
  interrupt('next_utterance');
  const id = ++current;
  queue.reset(id);
  const row = document.createElement('article'); row.className = 'turn'; row.dataset.turn = String(id);
  const label = document.createElement('small'); label.textContent = `TURN ${id} · ${source === 'wav' ? 'WAV 개발 입력' : '마이크'}`;
  const user = document.createElement('p'); user.className = 'user'; user.textContent = '음성을 인식하고 있어요…';
  const assistant = document.createElement('p'); assistant.className = 'assistant';
  row.append(label, user, assistant); $('conversation').querySelector('.empty')?.remove(); $('conversation').append(row);
  const speechEnd = source === 'vad' ? Math.max(0, vadEnd - Number(($('silence') as HTMLInputElement).value)) : vadEnd;
  turns.set(id, { id, vadEnd, speechEnd, sentenceRole: new Map(), user, assistant, done: false, cancelled: false, finished: false, handoff: false, deciding: activeDetector === 'vad+smartturn' });
  send({ type: 'utterance', turn_num: id, samples: audio.length, t_vad_end: vadEnd, c_speech_end_est: speechEnd, source });
  socket.send(microphoneFrame(audio, id));
  log('c_vad_end', { turn_num: id, c_vad_end: vadEnd, c_speech_end_est: speechEnd, source, samples: audio.length });
  status('예약 정보를 확인하고 있어요');
}
function receive(event: MessageEvent) {
  try {
    if (event.data instanceof ArrayBuffer) {
      const audio = readAudio(event.data), turn = turns.get(audio.turn);
      if (audio.turn === current && turn && !turn.cancelled && !turn.finished) { queue?.add(audio.samples, audio.seq); thresholds(); }
      else log('stale_audio_discarded', { turn_num: audio.turn });
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === 'session.ready') {
      activeDetector = message.turn_detect || 'vad';
      ready = true; connecting = false; controls(); status(micMode ? '듣고 있어요' : 'WAV 입력 대기');
      log('session.ready', { session_id: message.session_id, settings: message.settings });
      if (micMode) void startMic(epoch).catch(error => { fail(error); void disconnect(); });
      return;
    }
    if (message.type === 'error') {
      fail(message.message);
      const affected = turns.get(message.turn_num);
      if (affected && message.turn_num === current) { affected.cancelled = true; queue?.stop(); thresholds(); }
      if (connecting) { connecting = false; ready = false; controls(); }
      status('오류 · 설정 또는 연결을 확인하세요'); return;
    }
    if (message.type === 'turn.cancelled') log('turn.cancelled', { turn_num: message.turn_num, reason: message.reason });
    const turn = turns.get(message.turn_num);
    if (!turn || message.turn_num !== current || turn.cancelled) return;
    if (message.type === 'turn.waiting') { status('말씀이 이어지는지 잠시 기다리고 있어요'); log('turn.waiting', message); }
    if (message.type === 'stt.result') { turn.deciding = false; turn.user.textContent = message.text; }
    if (message.type === 'llm.delta') turn.assistant.textContent += message.text;
    if (message.type === 'tts.sentence') turn.sentenceRole.set(message.first_chunk_seq, message.role);
    if (message.type === 'turn.cancelled') { turn.cancelled = true; queue?.stop(); thresholds(); status('응답이 중단되었습니다'); }
    if (message.type === 'turn.done') {
      turn.done = true; turn.handoff = !!message.handoff_queued;
      const m = message.marks || {};
      const delta = (end: string, start: string) => typeof m[end] === 'number' && typeof m[start] === 'number' ? `${Math.round(m[end] - m[start])} ms` : '—';
      $('server-metrics').textContent = `턴 결정 ${delta('s_turn_decided','s_rx')} · STT ${delta('s_stt_done','s_turn_decided')} · LLM 첫 토큰 ${delta('s_llm_first_token','s_stt_done')} · 문장 ${delta('s_first_sentence','s_llm_first_token')} · TTS 첫 오디오 ${delta('s_tts_first_audio','s_first_sentence')}`;
      log('turn.done', { turn_num: turn.id, marks: m, handoff_queued: turn.handoff }); queue?.generationDone();
    }
    $('conversation').scrollTop = $('conversation').scrollHeight;
  } catch (error) { fail(error); interrupt('protocol_error'); }
}
async function startMic(generation: number) {
  if (vad?.listening) return;
  status('마이크와 음성 감지 준비 중');
  if (!vad) {
    const { MicVAD } = await import('@ricky0123/vad-web');
    const candidate = await MicVAD.new({ model: 'v5', startOnLoad: false, baseAssetPath: '/vad/', onnxWASMBasePath: '/vad/',
      redemptionMs: Number(($('silence') as HTMLInputElement).value), minSpeechMs: 250, preSpeechPadMs: 300,
      getStream: () => navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } }),
      onSpeechRealStart: () => { if (ready) { interrupt(); status('말씀하고 있어요'); } },
      onSpeechEnd: audio => { if (ready) { try { utterance(audio, 'vad'); } catch (error) { fail(error); } } },
      onVADMisfire: () => log('vad.misfire'),
    });
    if (generation !== epoch || !ready) { await candidate.destroy(); return; }
    vad = candidate;
  }
  await vad.start();
  if (generation !== epoch || !ready) { await vad.pause(); return; }
  thresholds(); status('듣고 있어요'); log('vad.ready', { model: 'v5', redemption_ms: Number(($('silence') as HTMLInputElement).value) });
}
async function connect(microphone: boolean) {
  if (socket || connecting) return;
  $('error').textContent = ''; connecting = true; micMode = microphone; const generation = ++epoch; controls();
  try {
    await prepareAudio();
    if (generation !== epoch || !connecting) return;
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/call`);
    socket = ws; ws.binaryType = 'arraybuffer'; controls(); status('선택한 엔진 준비 중');
    ws.onopen = () => { if (socket === ws) send(settings()); };
    ws.onmessage = event => { if (socket === ws) receive(event); };
    ws.onerror = () => { if (socket === ws) fail('서버 연결에 실패했습니다.'); };
    ws.onclose = () => { if (socket === ws) { void disconnect(); status('연결 종료 · 다시 시작할 수 있습니다'); } };
  } catch (error) { fail(error); await disconnect(); }
}
async function disconnect() {
  epoch++; ready = false; connecting = false; interrupt('disconnect');
  const ws = socket; socket = undefined; ws?.close(); queue?.stop();
  const oldVad = vad; vad = undefined; controls();
  await oldVad?.destroy(); status('통화 종료');
}
$('start').onclick = () => { void connect(true); };
$('connect').onclick = () => { void connect(false); };
$('stop').onclick = () => { void disconnect(); };
$('interrupt').onclick = () => interrupt('test');
$('apply').onclick = () => {
  interrupt('settings'); ready = false; connecting = true; controls(); status('설정 적용 중');
  void (async () => { await vad?.pause(); send(settings()); })().catch(fail);
};
$('silence').oninput = () => { $('silence-value').textContent = `${($('silence') as HTMLInputElement).value} ms`; thresholds(); };
$('echo').onchange = thresholds;
$('engine').onchange = () => {
  const engine = ($('engine') as HTMLSelectElement).value;
  $('engine-hint').textContent = ['voxcpm2','cosyvoice3'].includes(engine) ? '느림(체험용) · T7 첫 음성 6~11초. 서버에서 선택 엔진 실행이 필요합니다.' : engine === 'kokoro' ? 'Kokoro는 프리셋 음성을 사용합니다.' : 'Qwen3-TTS는 공통 참조 음성을 사용합니다.';
};
$('wav').onchange = () => {
  const input = $('wav') as HTMLInputElement, file = input.files?.[0]; input.value = '';
  if (!file) return;
  const generation = epoch;
  void (async () => {
    if (file.size > 25 * 1024 * 1024) throw new Error('WAV 파일은 25 MB 이하여야 합니다.');
    await prepareAudio();
    const decoded = await context!.decodeAudioData(await file.arrayBuffer());
    if (decoded.duration > 60) throw new Error('WAV는 60초 이하여야 합니다.');
    const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * 16000)), 16000);
    const source = offline.createBufferSource(); source.buffer = decoded; source.connect(offline.destination); source.start();
    const pcm = await offline.startRendering();
    if (generation === epoch && ready) utterance(pcm.getChannelData(0), 'wav');
  })().catch(fail);
};
$('download').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(events, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'zivo-call-client-log.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
};
window.addEventListener('pagehide', () => { void disconnect(); });
