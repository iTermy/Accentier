// Microphone capture -> mono 16-bit WAV blob.
//
// We deliberately do NOT use MediaRecorder: it produces webm/opus whose
// server-side decoding is fragile. Instead an AudioWorklet streams raw
// Float32 PCM to the main thread and we encode a WAV ourselves — lossless,
// codec-free, works identically in every browser that has getUserMedia.

const WORKLET_SOURCE = `
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("accentier-capture", CaptureProcessor);
`;

export class Recorder {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];
  private analyser: AnalyserNode | null = null;
  sampleRate = 48000;

  async start(): Promise<void> {
    this.chunks = [];
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
    this.ctx = new AudioContext();
    this.sampleRate = this.ctx.sampleRate;
    const source = this.ctx.createMediaStreamSource(this.stream);

    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    source.connect(this.analyser);

    if (this.ctx.audioWorklet) {
      const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "application/javascript" }));
      await this.ctx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);
      const node = new AudioWorkletNode(this.ctx, "accentier-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 0,
      });
      node.port.onmessage = (e: MessageEvent<Float32Array>) => this.chunks.push(e.data);
      source.connect(node);
      this.node = node;
    } else {
      // ancient-browser fallback
      const node = (this.ctx as any).createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (e: AudioProcessingEvent) =>
        this.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      source.connect(node);
      node.connect(this.ctx.destination);
      this.node = node;
    }
  }

  /** 0..1 instantaneous input level for the meter. */
  level(): number {
    if (!this.analyser) return 0;
    const buf = new Uint8Array(this.analyser.fftSize);
    this.analyser.getByteTimeDomainData(buf);
    let peak = 0;
    for (const v of buf) peak = Math.max(peak, Math.abs(v - 128) / 128);
    return peak;
  }

  async stop(): Promise<Blob> {
    const total = this.chunks.reduce((n, c) => n + c.length, 0);
    const pcm = new Float32Array(total);
    let off = 0;
    for (const c of this.chunks) {
      pcm.set(c, off);
      off += c.length;
    }
    this.teardown();
    return encodeWav(pcm, this.sampleRate);
  }

  cancel(): void {
    this.teardown();
    this.chunks = [];
  }

  private teardown(): void {
    try {
      if (this.node) {
        (this.node as any).port?.close?.();
        this.node.disconnect();
      }
      this.stream?.getTracks().forEach((t) => t.stop());
      this.ctx?.close();
    } catch {}
    this.node = null;
    this.stream = null;
    this.ctx = null;
    this.analyser = null;
  }
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (o: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++, off += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}
