import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, type Verdict } from '../api'
import { toJpeg } from '../images'
import { Button, Card, Chip, ErrorNote, Page, ProductImage, Spinner, buttonClass, productTitle, shortBrand } from '../ui'

type Msg = {
  id: number
  role: 'user' | 'assistant'
  text: string
  productKey?: string
}

const FRAME_MS = 1000
const POLL_MS = 1500

function newSession() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')
}

function speak(text: string) {
  try {
    speechSynthesis.cancel()
    speechSynthesis.speak(new SpeechSynthesisUtterance(text))
  } catch {
    /* audio not available */
  }
}

export default function Live() {
  const video = useRef<HTMLVideoElement>(null)
  const [sessionId] = useState(newSession)
  const [ready, setReady] = useState(false)
  const [cameraError, setCameraError] = useState<string | null>(null)
  const [feed, setFeed] = useState<'starting' | 'live' | 'stale' | 'off'>('starting')
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [verdicts, setVerdicts] = useState<Verdict[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [jobNote, setJobNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  const [muted, setMuted] = useState(false)
  const idRef = useRef(1)
  const busyRef = useRef(false)
  const sessionRef = useRef(sessionId)
  sessionRef.current = sessionId

  const say = useCallback((role: Msg['role'], text: string, productKey?: string) => {
    setMsgs((m) => [...m, { id: idRef.current++, role, text, productKey }])
  }, [])

  // Camera + 1fps frame upload. Fire-and-forget; skips a tick while one is in flight.
  useEffect(() => {
    let stream: MediaStream | undefined
    let stopped = false
    let timer: number | undefined
    let inFlight = false
    let lastOk = 0

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        })
      } catch {
        if (!stopped) {
          setCameraError("Couldn't open the camera. Allow camera access to use live mode.")
          setFeed('off')
        }
        return
      }
      if (stopped || !video.current) return
      video.current.srcObject = stream
      await video.current.play().catch(() => {})
      if (stopped) return
      setReady(true)

      const tick = async () => {
        if (stopped) return
        if (document.hidden || !video.current || video.current.videoWidth === 0) {
          timer = window.setTimeout(tick, FRAME_MS)
          return
        }
        if (!inFlight) {
          inFlight = true
          try {
            const blob = await toJpeg(video.current, 800)
            await api.liveFrame(sessionRef.current, blob)
            lastOk = Date.now()
            if (!stopped) setFeed('live')
          } catch {
            if (!stopped && Date.now() - lastOk > 5000) setFeed('stale')
          } finally {
            inFlight = false
          }
        } else if (Date.now() - lastOk > 5000) {
          setFeed('stale')
        }
        timer = window.setTimeout(tick, FRAME_MS)
      }
      tick()
    }
    start()
    return () => {
      stopped = true
      window.clearTimeout(timer)
      stream?.getTracks().forEach((t) => t.stop())
    }
  }, [])

  async function pollJob(jobId: string) {
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, POLL_MS))
      try {
        const job = await api.job(jobId)
        if (job.status === 'done') return job
        if (job.status === 'failed') return job
      } catch {
        return null
      }
    }
    return null
  }

  const ask = useCallback(async (question?: string) => {
    if (busyRef.current) return
    const q = (question ?? input).trim() || 'Tell me about this'
    busyRef.current = true
    setBusy(true)
    setError(null)
    setJobNote(null)
    say('user', q)
    setInput('')
    try {
      // Fresh frame first so the tool sees what the user sees now.
      if (video.current && video.current.videoWidth > 0) {
        try {
          await api.liveFrame(sessionRef.current, await toJpeg(video.current, 800))
        } catch {
          /* lookup still uses the last good frame */
        }
      }
      const res = await api.liveLookup(sessionRef.current)
      say('assistant', res.answer, res.product_key ?? undefined)
      if (!muted) speak(res.answer)
      if (res.found && res.product_key) {
        const v = await api.product(res.product_key)
        setVerdicts((vs) => [...vs.slice(-1), v])
      } else if (res.job_id) {
        setJobNote('Looking it up in the background… keep pointing at the pack.')
        const job = await pollJob(res.job_id)
        setJobNote(null)
        const verdict = job?.result?.verdict
        const outcome = job?.result?.result
        if (verdict) {
          setVerdicts((vs) => [...vs.slice(-1), verdict])
          const line = `Found it: ${productTitle(verdict.product)}. ${verdict.headline[0] ?? ''}`
          say('assistant', line, verdict.product.key)
          if (!muted) speak(line)
        } else if (outcome?.status === 'not_food') {
          say('assistant', "That doesn't look like a packaged food. Point at the front of the pack?")
        } else if (outcome) {
          say('assistant', 'I still couldn\'t find reliable ingredients for this one. A photo of the back label would help.')
        }
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong. Try again.')
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }, [input, muted, say])

  function toggleListen() {
    const Rec = (window as unknown as { SpeechRecognition?: new () => SpeechRec; webkitSpeechRecognition?: new () => SpeechRec }).SpeechRecognition
      ?? (window as unknown as { webkitSpeechRecognition?: new () => SpeechRec }).webkitSpeechRecognition
    if (!Rec) {
      setError('Voice input isn\'t supported in this browser. Type your question instead.')
      return
    }
    if (listening) return
    const rec = new Rec()
    rec.lang = 'en-IN'
    rec.interimResults = false
    rec.maxAlternatives = 1
    setListening(true)
    rec.onresult = (e: { results: { transcript: string }[][] }) => {
      const text = e.results[0][0].transcript
      setListening(false)
      ask(text)
    }
    rec.onerror = rec.onend = () => setListening(false)
    try {
      rec.start()
    } catch {
      setListening(false)
    }
  }

  interface SpeechRec {
    lang: string
    interimResults: boolean
    maxAlternatives: number
    onresult: ((e: { results: { transcript: string }[][] }) => void) | null
    onerror: (() => void) | null
    onend: (() => void) | null
    start(): void
  }

  const pair = verdicts.slice(-2)

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">Live</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">Point, ask, compare</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-muted">
          Keep the camera on the pack and ask. Point at a second pack to compare it with the last one.
        </p>
      </div>

      <div className="rise relative mt-6 aspect-[4/3] overflow-hidden rounded-3xl border border-line bg-ink shadow-card">
        <video ref={video} playsInline muted className="size-full object-cover" />
        {!ready && !cameraError && (
          <div className="absolute inset-0 flex items-center justify-center text-white/80"><Spinner /></div>
        )}
        {cameraError && (
          <div className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-white/90">{cameraError}</div>
        )}
        <div className="absolute top-3 left-3">
          <FeedChip feed={feed} />
        </div>
      </div>

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}

      {pair.length === 2 && <CompareCard a={pair[0]} b={pair[1]} />}

      {verdicts.length > 0 && (
        <div className="rise mt-4 space-y-3">
          {verdicts.slice(-2).map((v) => (
            <Link key={v.product.key} to={`/p/${encodeURIComponent(v.product.key)}`} className="block">
              <Card className="flex items-center gap-3">
                <ProductImage
                  productKey={v.product.key}
                  hasImage={!!(v.product.image_file_id || v.product.image_url || (v.product.source === 'off' && v.product.barcode))}
                  seed={shortBrand(v.product.brand) || productTitle(v.product)}
                  label={shortBrand(v.product.brand) || 'Product'}
                  className="h-16 w-13 shrink-0 rounded-2xl"
                />
                <div className="min-w-0">
                  <p className="truncate text-[15px] font-medium">{productTitle(v.product)}</p>
                  <p className="line-clamp-2 text-sm text-muted">{v.headline[0]}</p>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}

      <div className="rise mt-4 space-y-2">
        {msgs.length === 0 && (
          <p className="text-center text-sm text-faint">Try: “Tell me about this” → point at another pack → “Which has less sugar?”</p>
        )}
        {msgs.map((m) => (
          <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <p className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed ${m.role === 'user' ? 'bg-accent text-white' : 'bg-white border border-line text-ink-2 shadow-card'}`}>
              {m.text}
            </p>
          </div>
        ))}
        {jobNote && <p className="flex items-center justify-center gap-2 py-2 text-sm text-muted"><Spinner className="text-accent" />{jobNote}</p>}
      </div>

      <form
        className="rise mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          ask()
        }}
      >
        <button
          type="button"
          onClick={toggleListen}
          aria-label="Ask by voice"
          className={`${buttonClass(listening ? 'primary' : 'secondary', 'px-4')} ${listening ? 'animate-pulse' : ''}`}
        >
          {listening ? 'Listening…' : '🎤'}
        </button>
        <label htmlFor="live-q" className="sr-only">Ask about the pack</label>
        <input
          id="live-q"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Tell me about this"
          className="min-w-0 flex-1 rounded-full border border-line bg-white px-4 py-2.5 text-[15px] shadow-card outline-none placeholder:text-faint focus:border-accent"
        />
        <button type="submit" className={buttonClass('primary')} disabled={busy}>
          {busy ? <Spinner /> : 'Ask'}
        </button>
      </form>
      <div className="mt-2 flex justify-center gap-2">
        <button type="button" onClick={() => setMuted((m) => !m)} className={buttonClass('ghost', 'text-xs')}>
          {muted ? 'Unmute answers' : 'Mute answers'}
        </button>
        <Link to="/scan" className={buttonClass('ghost', 'text-xs')}>Back to scan</Link>
      </div>
    </Page>
  )
}

function FeedChip({ feed }: { feed: 'starting' | 'live' | 'stale' | 'off' }) {
  const tone = feed === 'live' ? 'good' : feed === 'stale' ? 'warn' : 'neutral'
  const label = feed === 'live' ? '● Live · 1 fps' : feed === 'stale' ? '● Feed stale' : feed === 'off' ? 'Camera off' : 'Starting…'
  return <Chip tone={tone}>{label}</Chip>
}

function CompareCard({ a, b }: { a: Verdict; b: Verdict }) {
  const flagged = (v: Verdict) => v.additives.filter((x) => x.flagged).map((x) => x.code || x.name)
  const fa = flagged(a)
  const fb = flagged(b)
  const onlyA = fa.filter((c) => !fb.includes(c))
  const onlyB = fb.filter((c) => !fa.includes(c))
  const sugars = (v: Verdict) => v.product.nutrition.sugars_g
  const sa = sugars(a)
  const sb = sugars(b)
  let line: string
  if (sa !== null && sb !== null && sa !== sb) {
    const [lo, hi] = sa < sb ? [a, b] : [b, a]
    line = `${productTitle(lo.product)} has less sugar (${lo.product.nutrition.sugars_g} g) than ${productTitle(hi.product)} (${hi.product.nutrition.sugars_g} g).`
  } else if (onlyA.length || onlyB.length) {
    line = [onlyA.length ? `${productTitle(a.product)} flags ${onlyA.join(', ')}` : null,
      onlyB.length ? `${productTitle(b.product)} flags ${onlyB.join(', ')}` : null]
      .filter(Boolean).join('; ') + '.'
  } else {
    line = 'Same flags on both — check the claims for the difference.'
  }
  return (
    <Card className="rise mt-4 border-accent/30 bg-accent-wash/40">
      <p className="text-xs font-medium tracking-wide text-accent-deep uppercase">Last two compared</p>
      <p className="mt-2 text-[15px] leading-relaxed text-ink-2">{line}</p>
      <div className="mt-3 grid grid-cols-2 gap-3">
        {[a, b].map((v) => (
          <Link key={v.product.key} to={`/p/${encodeURIComponent(v.product.key)}`} className="rounded-2xl bg-white p-3 shadow-card">
            <p className="truncate text-sm font-medium">{productTitle(v.product)}</p>
            <p className="mt-0.5 text-xs text-faint">
              {flagged(v).length ? `Flags: ${flagged(v).join(', ')}` : 'No flags'}
            </p>
          </Link>
        ))}
      </div>
      <Button variant="secondary" className="mt-3" onClick={() => speak(line)}>🔊 Hear it</Button>
    </Card>
  )
}
