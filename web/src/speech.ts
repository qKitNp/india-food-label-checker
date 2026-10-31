// Cuts a live audio stream into utterances: starts recording when the level rises,
// stops after a short silence, and hands back the clip for captioning.

export type SpeechWatchOptions = {
  threshold: number // RMS level that counts as speech (0..1)
  silenceMs?: number // quiet this long ends the utterance
  minMs?: number // shorter blips (a cough, a click) are dropped
  maxMs?: number // long monologues are cut so captions keep up
  enabled?: () => boolean // e.g. not while muted
  boost?: () => number // multiply the threshold, e.g. while the other side is talking
  onStart: () => void
  onEnd: (clip: Blob | null) => void
}

const PRE_ROLL_S = 0.35
const MIME = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm']

export function watchSpeech(stream: MediaStream, o: SpeechWatchOptions): () => void {
  const mimeType = MIME.find((m) => typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(m))
  if (!mimeType) return () => {}
  const silenceMs = o.silenceMs ?? 700
  const minMs = o.minMs ?? 350
  const maxMs = o.maxMs ?? 12000

  const ctx = new AudioContext()
  ctx.resume().catch(() => {})
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 1024
  const source = ctx.createMediaStreamSource(stream)
  source.connect(analyser)
  // Record a slightly delayed copy: by the time the level trips the detector, the
  // recording still holds the start of the first word.
  const delay = ctx.createDelay(1)
  delay.delayTime.value = PRE_ROLL_S
  const tap = ctx.createMediaStreamDestination()
  source.connect(delay).connect(tap)
  const buf = new Float32Array(analyser.fftSize)

  let rec: MediaRecorder | null = null
  let loudSince = 0
  let started = 0
  let lastLoud = 0

  const stop = () => {
    const r = rec
    rec = null
    if (r && r.state !== 'inactive') r.stop()
  }

  const timer = window.setInterval(() => {
    analyser.getFloatTimeDomainData(buf)
    let sum = 0
    for (const v of buf) sum += v * v
    const rms = Math.sqrt(sum / buf.length)
    const now = performance.now()

    if (!rec) {
      const threshold = o.threshold * (o.boost?.() ?? 1)
      if ((o.enabled && !o.enabled()) || rms < threshold) {
        loudSince = 0
        return
      }
      if (!loudSince) loudSince = now
      if (now - loudSince < 120) return
      const chunks: Blob[] = []
      const r = new MediaRecorder(tap.stream, { mimeType })
      r.ondataavailable = (e) => e.data.size && chunks.push(e.data)
      const t0 = now
      r.onstop = () => {
        const spoke = lastLoud - t0
        o.onEnd(spoke >= minMs ? new Blob(chunks, { type: r.mimeType || mimeType }) : null)
      }
      r.start()
      rec = r
      started = lastLoud = now
      o.onStart()
      return
    }
    if (rms >= o.threshold * 0.6) lastLoud = now
    if (now - lastLoud > silenceMs || now - started > maxMs) stop()
  }, 50)

  return () => {
    window.clearInterval(timer)
    stop()
    ctx.close().catch(() => {})
  }
}
