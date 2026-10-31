import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Check, Mic, MicOff, Phone, PhoneOff, SwitchCamera, Volume2, VolumeX } from 'lucide-react'
import { API_URL, api, ApiError, liveSnapUrl, type Verdict } from '../api'
import { toJpeg } from '../images'
import { Button, Card, Chip, ErrorNote, Page, ProductImage, Spinner, buttonClass, productTitle, shortBrand } from '../ui'

type SnapStatus = 'looking' | 'researching' | 'done' | 'missed'

type Msg = {
  id: number
  role: 'user' | 'assistant'
  text: string
  productKey?: string
  // The exact frame a lookup is working on, frozen in the chat.
  snap?: { url: string; status: SnapStatus }
}

const FRAME_MS = 1000
const POLL_MS = 1500
// While on a call: how often to check whether Bolna started a lookup. The snap
// shows up at most this long after the tool call, well before the answer.
const CALL_POLL_MS = 1000
const FREEZE_MS = 1400

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
  const chatRef = useRef<HTMLDivElement>(null)
  const agentAudio = useRef<HTMLAudioElement>(null)
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
  const [muted, setMuted] = useState(false)
  const [callState, setCallState] = useState<'idle' | 'connecting' | 'ringing' | 'active' | 'ended'>('idle')
  const [callError, setCallError] = useState<string | null>(null)
  const [callMuted, setCallMuted] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [callSecs, setCallSecs] = useState(0)
  const [endReason, setEndReason] = useState<string | null>(null)
  const idRef = useRef(1)
  const busyRef = useRef(false)
  const callRef = useRef<{ stop(): Promise<void>; setMuted(b: boolean): void } | null>(null)
  const onCallRef = useRef(false)
  const seenAtRef = useRef(0)
  const lastSnapAtRef = useRef(0) // server time of the newest snap shown
  const snapMsgRef = useRef(new Map<number, number>()) // snap_at -> chat message id
  const lastBlobRef = useRef<Blob | null>(null) // last frame uploaded, for typed asks
  const ownSnapsRef = useRef(new Set<number>()) // snaps from typed asks, already in the chat
  const [freeze, setFreeze] = useState<{ url: string; key: number } | null>(null)
  // Front cameras (laptops, selfie mode) are shown mirrored, like every video-call app.
  // Only the preview is flipped: frames sent for lookup stay as the camera sees them.
  const [mirror, setMirror] = useState(false)
  // Camera choice: null = the default (rear on phones); otherwise a specific device.
  const [deviceId, setDeviceId] = useState<string | null>(null)
  const [cameras, setCameras] = useState<string[]>([])
  const currentCamRef = useRef<string | null>(null)
  const autoCallRef = useRef(false)
  // Snaps whose final answer is already in the chat. During a call both Bolna's wait loop
  // and the page's own job poll learn the result; only the first one gets to say it.
  const announcedRef = useRef(new Set<number>())
  const claimAnswer = (snapId: number | undefined) => {
    if (snapId === undefined) return true
    if (announcedRef.current.has(snapId)) return false
    announcedRef.current.add(snapId)
    return true
  }

  const say = useCallback((role: Msg['role'], text: string, productKey?: string) => {
    setMsgs((m) => [...m, { id: idRef.current++, role, text, productKey }])
  }, [])

  // Freeze the frame over the camera and pin it in the chat: "this is the pack I'm on".
  const showSnap = useCallback((url: string) => {
    const id = idRef.current++
    setMsgs((m) => [...m, { id, role: 'assistant', text: '', snap: { url, status: 'looking' } }])
    setFreeze({ url, key: id })
    return id
  }, [])

  const setSnapStatus = useCallback((msgId: number | undefined, status: SnapStatus) => {
    if (msgId === undefined) return
    setMsgs((m) => m.map((x) => (x.id === msgId && x.snap ? { ...x, snap: { ...x.snap, status } } : x)))
  }, [])

  useEffect(() => {
    if (!freeze) return
    const t = window.setTimeout(() => setFreeze(null), FREEZE_MS)
    return () => window.clearTimeout(t)
  }, [freeze])

  // Keep the newest answer in view: the chat scrolls on its own, the page doesn't jump.
  useEffect(() => {
    const el = chatRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [msgs, jobNote])

  // "Mute answers" silences both voices: the call agent and the browser's read-out of typed answers.
  useEffect(() => {
    if (agentAudio.current) agentAudio.current.muted = muted
    if (muted) window.speechSynthesis?.cancel()
  }, [muted])

  const speakIfFree = useCallback((text: string) => {
    if (!muted && !onCallRef.current) speak(text)
  }, [muted])

  // Keep the last two distinct products: showing the same pack again just moves it last.
  const addVerdict = useCallback((v: Verdict) => {
    setVerdicts((vs) => [...vs.filter((x) => x.product.key !== v.product.key).slice(-1), v])
  }, [])

  const showProduct = useCallback(async (productKey: string) => {
    const v = await api.product(productKey)
    addVerdict(v)
    return v
  }, [addVerdict])

  const followBackgroundJob = useCallback(async (jobId: string, snapId?: number): Promise<boolean> => {
    setJobNote('Looking it up in the background… keep pointing at the pack.')
    const job = await pollJob(jobId)
    setJobNote(null)
    const verdict = job?.result?.verdict
    const outcome = job?.result?.result
    if (outcome && !claimAnswer(snapId)) return !!verdict
    if (verdict) {
      addVerdict(verdict)
      const line = `Found it: ${productTitle(verdict.product)}. ${verdict.headline[0] ?? ''}`
      say('assistant', line, verdict.product.key)
      speakIfFree(line)
      return true
    } else if (outcome?.status === 'not_food') {
      say('assistant', "That doesn't look like a packaged food. Point at the front of the pack?")
    } else if (outcome) {
      say('assistant', 'I still couldn\'t find reliable ingredients for this one. A photo of the back label would help.')
    }
    return false
  }, [say, speakIfFree, addVerdict])

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
          video: {
            ...(deviceId ? { deviceId: { exact: deviceId } } : { facingMode: 'environment' }),
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
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
      const settings = stream.getVideoTracks()[0]?.getSettings()
      setMirror(settings?.facingMode !== 'environment')
      currentCamRef.current = settings?.deviceId ?? null
      navigator.mediaDevices.enumerateDevices()
        .then((ds) => !stopped && setCameras(ds.filter((d) => d.kind === 'videoinput' && d.deviceId).map((d) => d.deviceId)))
        .catch(() => {})
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
            await api.liveFrame(sessionId, blob)
            lastBlobRef.current = blob
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
  }, [sessionId, deviceId])

  function switchCamera() {
    if (cameras.length < 2) return
    const i = cameras.indexOf(currentCamRef.current ?? '')
    setDeviceId(cameras[(i + 1) % cameras.length])
  }

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
          const blob = await toJpeg(video.current, 800)
          await api.liveFrame(sessionId, blob)
          lastBlobRef.current = blob
        } catch {
          /* lookup still uses the last good frame */
        }
      }
      const snapId = lastBlobRef.current ? showSnap(URL.createObjectURL(lastBlobRef.current)) : undefined
      const res = await api.liveLookup(sessionId)
      // A call running alongside must not show this snap and answer a second time.
      if (res.snap_at) ownSnapsRef.current.add(res.snap_at)
      say('assistant', res.answer, res.product_key ?? undefined)
      speakIfFree(res.answer)
      if (res.found && res.product_key) {
        setSnapStatus(snapId, 'done')
        await showProduct(res.product_key)
      } else if (res.job_id) {
        setSnapStatus(snapId, 'researching')
        setSnapStatus(snapId, (await followBackgroundJob(res.job_id, snapId)) ? 'done' : 'missed')
      } else {
        setSnapStatus(snapId, 'missed')
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong. Try again.')
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }, [input, say, sessionId, showProduct, followBackgroundJob, speakIfFree, showSnap, setSnapStatus])

  // Bolna voice call: same agent + tools as the phone path, over WebRTC.
  // The call's tool answers land in /live/result (the page can't hear the call),
  // so we poll it and render cards while talking.
  async function startCall() {
    if (callRef.current) return
    setCallError(null)
    // Warm up the mic inside the tap gesture: Brave/Chrome grant more reliably
    // here than inside the SDK's async start, and a denial shows our guidance.
    try {
      const warm = await navigator.mediaDevices.getUserMedia({ audio: true })
      warm.getTracks().forEach((t) => t.stop())
    } catch {
      setCallError('Microphone is blocked. Tap the lock icon → Site settings → Microphone → Allow, close any overlay or bubble apps, then try again. Chrome also works if Brave keeps refusing.')
      return
    }
    setCallState('connecting')
    setCallMuted(false)
    try {
      const { BolnaWebCall } = await import('@bolna/web-call')
      const call = new BolnaWebCall({ sessionUrl: `${API_URL}/live/session`, audioElement: agentAudio.current ?? undefined })
      callRef.current = call
      call.on('state-change', (s: string) => {
        setCallState(s as typeof callState)
        if (s === 'active') onCallRef.current = true
        if (s === 'ended') {
          onCallRef.current = false
          callRef.current = null
        }
      })
      call.on('error', (e: { code?: string; message?: string }) => {
        setCallError(
          e.code === 'microphone_denied'
            ? 'Microphone is blocked. Tap the lock icon → Site settings → Microphone → Allow, close any overlay or bubble apps, then try again. Chrome also works if Brave keeps refusing.'
            : (e.message ?? 'Call failed. Try again.'),
        )
        setCallState('ended')
        onCallRef.current = false
        callRef.current = null
      })
      call.on('volume-level', (v: number) => {
        // ~10 events/sec while active: only flip state on change to avoid re-renders.
        setSpeaking((prev) => {
          const s = v > 0.02
          return prev === s ? prev : s
        })
      })
      call.on('call-end', (e: { reason?: string }) => {
        const r = e.reason ?? ''
        setEndReason(
          r === 'local-hangup' ? 'You hung up.'
            : r === 'remote-hangup' ? 'Fineprint hung up after 45 s of quiet. Tap Call again to reconnect.'
              : r ? `Call ended (${r}). Tap Call again to reconnect.`
                : null,
        )
      })
      setEndReason(null)
      setCallSecs(0)
      setSpeaking(false)
      await call.start({ userData: { session_id: sessionId } })
    } catch (e) {
      callRef.current = null
      onCallRef.current = false
      setCallState('idle')
      setCallError(e instanceof Error ? e.message : 'Could not start the call.')
    }
  }

  async function stopCall() {
    try {
      await callRef.current?.stop()
    } finally {
      callRef.current = null
      onCallRef.current = false
      setCallState('ended')
    }
  }

  // Connect as soon as the page opens, like joining a video call. Browsers only let a
  // page play audio after the visitor has interacted with the site; a cold load
  // (pasted URL) gets a Start call button instead of a call nobody can hear.
  const startCallRef = useRef(startCall)
  useEffect(() => {
    startCallRef.current = startCall
  })
  useEffect(() => {
    if (autoCallRef.current || (!ready && !cameraError)) return
    autoCallRef.current = true
    const activation = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }).userActivation
    if (activation?.hasBeenActive ?? true) startCallRef.current()
  }, [ready, cameraError])

  // While on a Bolna call, mirror spoken answers into the chat + cards. The
  // moment Bolna's tool starts on a frame, freeze that frame so the caller
  // knows what's being looked at, long before the answer is spoken.
  useEffect(() => {
    if (callState !== 'active') return
    let stopped = false
    let timer: number | undefined
    const busyJobs = new Set<string>()
    const tick = async () => {
      if (stopped) return
      try {
        // A typed ask in flight shows its own snap and answer.
        const fed = busyRef.current ? null : await api.liveResult(sessionId)
        const snapAt = fed?.latest_snap_at ?? 0
        if (!stopped && fed && snapAt > lastSnapAtRef.current) {
          lastSnapAtRef.current = snapAt
          if (!ownSnapsRef.current.has(snapAt)) {
            snapMsgRef.current.set(snapAt, showSnap(liveSnapUrl(sessionId, snapAt)))
          }
        }
        if (!stopped && fed?.found && fed.answer && (fed.at ?? 0) > seenAtRef.current) {
          seenAtRef.current = fed.at ?? Date.now() / 1000
          const snapId = snapMsgRef.current.get(fed.snap_at ?? -1)
          if (fed.reason === 'researching' || fed.reason === 'idle') {
            // The agent's wait-loop progress: the snap bubble already says "Researching…".
            if (fed.reason === 'researching') setSnapStatus(snapId, 'researching')
          } else if (!ownSnapsRef.current.has(fed.snap_at ?? -1)) {
            // "New to me, looking it up" isn't final; a found product or a miss is.
            const final = !!fed.product_key || !fed.job_id
            if (!final || claimAnswer(snapId)) say('assistant', fed.answer, fed.product_key ?? undefined)
            if (fed.product_key) {
              setSnapStatus(snapId, 'done')
              showProduct(fed.product_key).catch(() => {})
            } else if (fed.job_id) {
              setSnapStatus(snapId, 'researching')
              const jobId = fed.job_id
              if (!busyJobs.has(jobId)) {
                busyJobs.add(jobId)
                followBackgroundJob(jobId, snapId)
                  .then((found) => setSnapStatus(snapId, found ? 'done' : 'missed'))
                  .catch(() => setSnapStatus(snapId, 'missed'))
                  .finally(() => busyJobs.delete(jobId))
              }
            } else {
              setSnapStatus(snapId, 'missed')
            }
          }
        }
      } catch {
        /* offline blip: next tick retries */
      }
      timer = window.setTimeout(tick, CALL_POLL_MS)
    }
    tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [callState, sessionId, say, showProduct, followBackgroundJob, showSnap, setSnapStatus])

  // Hang up if the user leaves the page mid-call.
  useEffect(() => () => {
    callRef.current?.stop().catch(() => {})
  }, [])

  // Call timer while active.
  useEffect(() => {
    if (callState !== 'active') return
    const t = window.setInterval(() => setCallSecs((s) => s + 1), 1000)
    return () => window.clearInterval(t)
  }, [callState])

  const mmss = `${Math.floor(callSecs / 60)}:${String(callSecs % 60).padStart(2, '0')}`

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

      <audio ref={agentAudio} autoPlay className="hidden" />
      <div className="rise relative mt-6 aspect-[3/4] overflow-hidden rounded-3xl border border-line bg-ink shadow-card sm:aspect-[4/3]">
        <video ref={video} playsInline muted className={`size-full object-cover ${mirror ? '-scale-x-100' : ''}`} />
        {!ready && !cameraError && (
          <div className="absolute inset-0 flex items-center justify-center text-white/80"><Spinner /></div>
        )}
        {cameraError && (
          <div className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-white/90">{cameraError}</div>
        )}
        {freeze && (
          <div key={freeze.key} className={`pointer-events-none absolute inset-0 ${mirror ? '-scale-x-100' : ''}`}>
            <img src={freeze.url} alt="" className="freeze absolute inset-0 size-full object-cover" />
            <div className="shutter absolute inset-0 bg-white" />
          </div>
        )}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-20 bg-gradient-to-b from-black/35 to-transparent" />
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-black/55 to-transparent" />
        <div className="absolute top-3 left-3 flex items-center gap-2">
          <FeedChip feed={feed} />
          {(callState === 'connecting' || callState === 'ringing' || callState === 'active') && (
            <span className="flex items-center gap-2 rounded-full bg-black/45 px-3 py-1 text-xs font-medium text-white backdrop-blur">
              {callState === 'active' ? (
                <>
                  <span className={`inline-block size-2 rounded-full ${speaking ? 'animate-ping bg-accent' : 'animate-pulse bg-good'}`} />
                  {speaking ? 'Speaking' : 'Listening'} · {mmss}
                </>
              ) : (
                <><Spinner className="size-3 text-white" /> Connecting…</>
              )}
            </span>
          )}
        </div>
        <div className="absolute top-3 right-3">
          <CallButton small label={muted ? 'Unmute answers' : 'Mute answers'} tone={muted ? 'on' : 'glass'} onClick={() => setMuted((m) => !m)}>
            {muted ? <VolumeX className="size-[18px]" aria-hidden /> : <Volume2 className="size-[18px]" aria-hidden />}
          </CallButton>
        </div>

        {/* Live-stream style chat: newest at the bottom, older lines fade out at the top. */}
        <div
          ref={chatRef}
          className="absolute inset-x-3 top-14 bottom-20 flex flex-col overflow-y-auto overscroll-contain [mask-image:linear-gradient(to_bottom,transparent,black_3rem)] [scrollbar-width:none]"
        >
          <div className="mt-auto space-y-1.5">
            {msgs.length === 0 && callState !== 'connecting' && (
              <p className="w-fit max-w-[85%] rounded-2xl bg-black/35 px-3 py-2 text-[13px] text-white/85 backdrop-blur-md">
                Try “Tell me about this”, then show another pack and ask “Which has less sugar?”
              </p>
            )}
            {msgs.map((m) => m.snap ? <SnapBubble key={m.id} {...m.snap} /> : (
              <div key={m.id} className={`rise flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <p className={`max-w-[85%] rounded-2xl px-3 py-2 text-[14px] leading-snug text-white backdrop-blur-md ${m.role === 'user' ? 'bg-accent/75' : 'bg-black/45'}`}>
                  {m.text}
                </p>
              </div>
            ))}
            {jobNote && (
              <p className="flex w-fit items-center gap-2 rounded-full bg-black/45 px-3 py-1.5 text-xs text-white backdrop-blur-md">
                <Spinner className="size-3 text-white" />{jobNote}
              </p>
            )}
          </div>
        </div>
        <div className="absolute inset-x-0 bottom-4 flex items-center justify-center gap-3">
          {callState === 'active' && (
            <CallButton
              label={callMuted ? 'Unmute' : 'Mute'}
              tone={callMuted ? 'on' : 'glass'}
              onClick={() => {
                const next = !callMuted
                setCallMuted(next)
                callRef.current?.setMuted(next)
              }}
            >
              {callMuted ? <MicOff className="size-5" aria-hidden /> : <Mic className="size-5" aria-hidden />}
            </CallButton>
          )}
          {callState === 'idle' || callState === 'ended' ? (
            <button
              type="button"
              onClick={startCall}
              className="flex h-12 items-center gap-2 rounded-full bg-good px-5 text-[15px] font-medium text-white shadow-lg transition-transform active:scale-95"
            >
              <Phone className="size-4" aria-hidden /> {callState === 'ended' ? 'Call again' : 'Start call'}
            </button>
          ) : (
            <CallButton label="End call" tone="bad" onClick={stopCall}>
              <PhoneOff className="size-5" aria-hidden />
            </CallButton>
          )}
          {cameras.length > 1 && (
            <CallButton label="Switch camera" tone="glass" onClick={switchCamera}>
              <SwitchCamera className="size-5" aria-hidden />
            </CallButton>
          )}
        </div>
      </div>

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}
      {callError && <div className="mt-4"><ErrorNote>{callError}</ErrorNote></div>}
      {endReason && callState === 'ended' && (
        <p className="rise mt-3 text-center text-sm text-muted">{endReason}</p>
      )}

      <form
        className="rise mt-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          ask()
        }}
      >
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

    </Page>
  )
}

const SNAP_LABEL: Record<SnapStatus, string> = {
  looking: 'Looking at this pack…',
  researching: 'New to me. Researching it…',
  done: 'Got it',
  missed: "Couldn't place this one",
}

function SnapBubble({ url, status }: { url: string; status: SnapStatus }) {
  const working = status === 'looking' || status === 'researching'
  return (
    <div className="rise flex justify-start">
      <div className="flex items-center gap-2.5 rounded-2xl bg-black/45 p-1.5 pr-3.5 backdrop-blur-md">
        <div className={`relative h-12 w-16 shrink-0 overflow-hidden rounded-xl bg-black/30 ${working ? 'ring-2 ring-accent' : ''}`}>
          <img src={url} alt="The frame being looked up" className="size-full object-cover" />
          {working && <div className="scanline absolute inset-x-0 h-0.5 bg-accent shadow-[0_0_8px_2px] shadow-accent/60" />}
        </div>
        <p className={`flex items-center gap-2 text-[13px] ${status === 'done' ? 'text-green-300' : status === 'missed' ? 'text-white/70' : 'text-sky-200'}`}>
          {working && <Spinner className="size-3 text-sky-200" />}
          {status === 'done' && <Check className="size-4" aria-hidden />}
          {SNAP_LABEL[status]}
        </p>
      </div>
    </div>
  )
}

function CallButton({ label, tone, onClick, small, children }: { label: string; tone: 'glass' | 'bad' | 'on'; onClick: () => void; small?: boolean; children: ReactNode }) {
  const look = tone === 'bad' ? 'bg-bad text-white' : tone === 'on' ? 'bg-white text-ink' : 'bg-white/20 text-white backdrop-blur hover:bg-white/30'
  return (
    <button type="button" onClick={onClick} aria-label={label} title={label}
      className={`flex ${small ? 'size-10' : 'size-12'} items-center justify-center rounded-full shadow-lg transition-transform active:scale-95 ${look}`}>
      {children}
    </button>
  )
}

function FeedChip({ feed }: { feed: 'starting' | 'live' | 'stale' | 'off' }) {
  const tone = feed === 'live' ? 'good' : feed === 'stale' ? 'warn' : 'neutral'
  const label = feed === 'live' ? '● Live' : feed === 'stale' ? '● Feed stale' : feed === 'off' ? 'Camera off' : 'Starting…'
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
      <Button variant="secondary" className="mt-3" onClick={() => speak(line)}><Volume2 className="size-4" aria-hidden /> Hear it</Button>
    </Card>
  )
}
