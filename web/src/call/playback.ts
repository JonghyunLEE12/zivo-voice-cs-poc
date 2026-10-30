type Clip = { source: AudioBufferSourceNode; start: number; end: number; timer: number; seq: number; acknowledged: boolean };
/** One active turn. AudioContext time, never network arrival, drives acknowledgments. */
export class PlaybackQueue {
  private clips = new Set<Clip>();
  private segments: { start: number; end: number }[] = [];
  private nextTime = 0;
  private done = false;
  private finished = false;
  turn = 0;
  constructor(readonly context: AudioContext,
    private onStart: (turn: number, seq: number, time: number) => void,
    private onFinish: (turn: number) => void) {}
  get active() { return this.clips.size > 0; }
  reset(turn: number) {
    this.stop(); this.turn = turn; this.done = false; this.finished = false; this.segments = [];
  }
  add(samples: Float32Array, seq: number) {
    const buffer = this.context.createBuffer(1, samples.length, 24000);
    buffer.getChannelData(0).set(samples);
    const source = this.context.createBufferSource();
    source.buffer = buffer; source.connect(this.context.destination);
    const start = Math.max(this.context.currentTime + 0.02, this.nextTime);
    const clip: Clip = { source, start, end: start + buffer.duration, timer: 0, seq, acknowledged: false };
    const turn = this.turn;
    this.nextTime = clip.end;
    this.segments.push({ start, end: clip.end });
    this.clips.add(clip);
    const acknowledge = () => {
      if (!this.clips.has(clip) || clip.acknowledged) return;
      if (this.context.currentTime < start || this.context.state !== 'running') {
        clip.timer = window.setTimeout(acknowledge, 4); return;
      }
      clip.acknowledged = true;
      // Translate the AudioContext render-clock start into the client's clock domain.
      this.onStart(turn, seq, performance.now() - (this.context.currentTime - start) * 1000);
    };
    source.onended = () => {
      acknowledge(); clearTimeout(clip.timer); this.clips.delete(clip); source.disconnect(); this.maybeFinish();
    };
    source.start(start);
    clip.timer = window.setTimeout(acknowledge, Math.max(0, (start - this.context.currentTime) * 1000));
  }
  generationDone() { this.done = true; this.maybeFinish(); }
  private maybeFinish() {
    if (this.done && !this.active && !this.finished) {
      this.finished = true; this.onFinish(this.turn);
    }
  }
  stop() {
    const now = this.context.currentTime;
    const playedMs = this.segments.reduce((sum, clip) => sum + Math.max(0, Math.min(now, clip.end) - clip.start) * 1000, 0);
    for (const clip of this.clips) {
      clearTimeout(clip.timer); clip.source.onended = null;
      clip.source.stop(); clip.source.disconnect();
    }
    this.clips.clear(); this.nextTime = 0; this.done = false;
    return playedMs;
  }
}
