/** Call v1: all integer fields and PCM samples are little endian. */
export function microphoneFrame(audio: Float32Array, turn: number): ArrayBuffer {
  const buffer = new ArrayBuffer(8 + audio.length * 2);
  const view = new DataView(buffer);
  view.setUint8(0, 1);
  view.setUint32(4, turn, true);
  audio.forEach((sample, i) => {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(8 + i * 2, Math.round(value * (value < 0 ? 32768 : 32767)), true);
  });
  return buffer;
}
export function readAudio(buffer: ArrayBuffer) {
  const view = new DataView(buffer);
  if (buffer.byteLength <= 8 || buffer.byteLength % 2 || view.getUint8(0) !== 2 || view.getUint8(1) !== 0) {
    throw new Error('잘못된 오디오 프레임');
  }
  const samples = new Float32Array((buffer.byteLength - 8) / 2);
  samples.forEach((_, i) => { samples[i] = view.getInt16(8 + i * 2, true) / 32768; });
  return { turn: view.getUint32(4, true), seq: view.getUint16(2, true), samples };
}
