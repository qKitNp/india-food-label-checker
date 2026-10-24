import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api'
import { getDetector } from '../barcode'
import { toJpeg, UnreadablePhoto } from '../images'
import { Button, ErrorNote, Page, Spinner } from '../ui'
import { CameraIcon } from './Home'

export default function Scan() {
  const navigate = useNavigate()
  const video = useRef<HTMLVideoElement>(null)
  const [cameraError, setCameraError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stream: MediaStream | undefined
    let stopped = false
    let timer: number | undefined

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } },
          audio: false,
        })
      } catch {
        setCameraError("Couldn't open the camera. Allow camera access, or upload a photo instead.")
        return
      }
      if (stopped || !video.current) return
      video.current.srcObject = stream
      await video.current.play().catch(() => {})
      setReady(true)

      const detector = await getDetector().catch(() => null)
      if (!detector) return
      let last = ''
      const tick = async () => {
        if (stopped || !video.current) return
        try {
          const [code] = await detector.detect(video.current)
          const digits = code?.rawValue.replace(/\D/g, '') ?? ''
          // Require two identical reads in a row to avoid acting on a misread.
          if (digits.length >= 8 && digits === last) {
            stopped = true
            navigator.vibrate?.(40)
            navigate(`/p/${digits}`)
            return
          }
          last = digits
        } catch {
          /* frame not ready */
        }
        timer = window.setTimeout(tick, 250)
      }
      tick()
    }
    start()

    return () => {
      stopped = true
      window.clearTimeout(timer)
      stream?.getTracks().forEach((t) => t.stop())
    }
  }, [navigate])

  async function identify(photo: Blob) {
    setBusy(true)
    setError(null)
    try {
      const { job_id } = await api.identify(await toJpeg(photo))
      navigate(`/job/${job_id}`)
    } catch (e) {
      setError(e instanceof ApiError || e instanceof UnreadablePhoto ? e.message : 'Something went wrong. Try again.')
      setBusy(false)
    }
  }

  async function snap() {
    if (!video.current) return
    setBusy(true)
    try {
      await identify(await toJpeg(video.current))
    } catch {
      setError("Couldn't take the photo. Try uploading one instead.")
      setBusy(false)
    }
  }

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">Scan</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">Point at the barcode</h1>
        <p className="mt-2 text-sm text-muted">It's read automatically. No barcode? Snap the front of the pack.</p>
      </div>

      <div className="rise relative mt-6 aspect-[3/4] overflow-hidden rounded-3xl border border-line bg-ink shadow-card">
        <video ref={video} playsInline muted className="size-full object-cover" />
        {ready && !busy && (
          <div className="pointer-events-none absolute inset-x-10 top-1/2 h-28 -translate-y-1/2 rounded-2xl border-2 border-white/80 shadow-[0_0_0_9999px_rgb(0_0_0/0.25)]" />
        )}
        {!ready && !cameraError && (
          <div className="absolute inset-0 flex items-center justify-center text-white/80">
            <Spinner />
          </div>
        )}
        {cameraError && (
          <div className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-white/90">{cameraError}</div>
        )}
        {busy && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-ink/60 text-white">
            <Spinner />
            <span className="text-sm">Sending photo…</span>
          </div>
        )}
      </div>

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}

      <div className="mt-5 flex flex-col gap-3">
        <Button onClick={snap} disabled={!ready || busy} className="py-3">
          <CameraIcon />
          Snap the front of the pack
        </Button>
        <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-full border border-line bg-white px-5 py-3 text-[15px] font-medium shadow-card hover:bg-wash">
          Upload a photo
          <input
            type="file"
            accept="image/*"
            className="sr-only"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) identify(file)
            }}
          />
        </label>
        <p className="text-center text-xs leading-relaxed text-faint">
          A photo of the front of the pack may be shown as the product's picture. Avoid faces and personal details.
        </p>
      </div>
    </Page>
  )
}
