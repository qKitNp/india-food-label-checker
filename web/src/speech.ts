// Notices when someone starts and stops talking on an audio stream, from its level.
// Used to show "it's hearing you" the instant the caller speaks; the words come from
// the call itself (see /live/captions).

export type SpeechWatchOptions = {
  threshold: number // RMS level that counts as speech (0..1)
  silenceMs?: number // quiet this long ends the utterance
  minMs?: number // shorter blips (a cough, a click) report as not speech
  enabled?: () => boolean // e.g. not while muted
  boost?: () => number // multiply the threshold, e.g. while the other side is talking
  onStart: () => void
  onEnd: (wasSpeech: boolean) => void
}

export function watchSpeech(stream: MediaStream, o: SpeechWatchOptions): () => void {
  const silenceMs = o.silenceMs ?? 700
  const minMs = o.minMs ?? 350

  const ctx = new AudioContext()
  ctx.resume().catch(() => {})
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 1024
  ctx.createMediaStreamSource(stream).connect(analyser)
  const buf = new Float32Array(analyser.fftSize)

  let talking = false
  let loudSince = 0
  let started = 0
  let lastLoud = 0

  const timer = window.setInterval(() => {
    analyser.getFloatTimeDomainData(buf)
    let sum = 0
    for (const v of buf) sum += v * v
    const rms = Math.sqrt(sum / buf.length)
    const now = performance.now()
    const threshold = o.threshold * (o.boost?.() ?? 1)

    if (!talking) {
      if ((o.enabled && !o.enabled()) || rms < threshold) {
        loudSince = 0
        return
      }
      if (!loudSince) loudSince = now
      if (now - loudSince < 120) return
      talking = true
      started = lastLoud = now
      o.onStart()
      return
    }
    if (rms >= threshold * 0.6) lastLoud = now
    if (now - lastLoud > silenceMs) {
      talking = false
      o.onEnd(lastLoud - started >= minMs)
    }
  }, 50)

  return () => {
    window.clearInterval(timer)
    if (talking) o.onEnd(false)
    ctx.close().catch(() => {})
  }
}
