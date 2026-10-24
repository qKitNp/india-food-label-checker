import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, type Job, type Tip } from '../api'
import { Card, ErrorNote, Page, Spinner, buttonClass } from '../ui'

const POLL_MS = 1500
const TIP_MS = 8000

// The agent's individual steps are logged server-side; here we only show coarse progress.
const STAGES: { key: Job['stage'][]; label: string }[] = [
  { key: ['queued', 'reading'], label: 'Reading the pack' },
  { key: ['matching'], label: 'Checking products we know' },
  { key: ['searching'], label: 'Looking up the ingredients' },
  { key: ['checking'], label: 'Checking the source' },
]

function stageIndex(stage: Job['stage'] | undefined) {
  const i = STAGES.findIndex((s) => stage && s.key.includes(stage))
  return i === -1 ? 0 : i
}

export default function JobPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    async function poll() {
      try {
        const next = await api.job(id)
        if (cancelled) return
        setJob(next)
        const product = next.result?.result.product
        if (next.status === 'done' && product && next.result?.verdict) {
          navigate(`/p/${encodeURIComponent(product.key)}`, { replace: true, state: { verdict: next.result.verdict } })
          return
        }
        if (next.status === 'done' || next.status === 'failed') return
      } catch (e) {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 404) {
          setError('This scan has expired. Please scan again.')
          return
        }
        // Network blips: keep polling.
      }
      timer = window.setTimeout(poll, POLL_MS)
    }
    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [id, navigate])

  const finished = job?.status === 'done' || job?.status === 'failed'
  const outcome = job?.result?.result
  const current = stageIndex(job?.stage)

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">Checking</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">
          {finished ? 'Done looking' : STAGES[current].label}
        </h1>
        {!finished && (
          <p className="mt-2 text-sm text-muted">New products can take up to a minute while we look them up.</p>
        )}
      </div>

      {!finished && (
        <div className="rise mx-auto mt-6 flex max-w-xs items-center gap-1.5" aria-hidden="true">
          {STAGES.map((s, i) => (
            <span
              key={s.label}
              className={`h-1.5 flex-1 rounded-full transition-colors duration-500 ${i < current ? 'bg-accent' : i === current ? 'animate-pulse bg-accent/60' : 'bg-line'}`}
            />
          ))}
        </div>
      )}

      {!finished && <Tips />}

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}

      {job?.status === 'failed' && (
        <div className="mt-6"><ErrorNote>Something went wrong while checking this product. Please try again.</ErrorNote></div>
      )}

      {outcome?.status === 'not_food' && (
        <p className="mt-6 text-center text-[15px] text-muted">That doesn't look like a packaged food or drink.</p>
      )}

      {outcome?.status === 'not_found' && (
        <div className="mt-6 text-center">
          <p className="text-[15px] text-muted">
            We couldn't find reliable ingredients for this product online. A photo of the back label works best.
          </p>
          <Link to="/label" className={buttonClass('primary', 'mt-4')}>Check the back label</Link>
        </div>
      )}

      {finished && (
        <div className="mt-4 text-center">
          <Link to="/scan" className={buttonClass('ghost')}>Scan something else</Link>
        </div>
      )}
    </Page>
  )
}

function shuffle<T>(items: T[]): T[] {
  const out = [...items]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

function domain(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** Rotating label-reading tips, each from a source we checked. Tap for the next one. */
function Tips() {
  const [tips, setTips] = useState<Tip[]>([])
  const [index, setIndex] = useState(0)

  useEffect(() => {
    api.tips().then((t) => setTips(shuffle(t)), () => setTips([]))
  }, [])

  useEffect(() => {
    if (tips.length < 2) return
    const timer = window.setInterval(() => setIndex((i) => (i + 1) % tips.length), TIP_MS)
    return () => window.clearInterval(timer)
  }, [tips.length])

  if (!tips.length) {
    return <div className="mt-10 flex justify-center text-accent"><Spinner /></div>
  }
  const tip = tips[index]
  return (
    <Card className="rise mt-8">
      <p className="text-xs font-medium tracking-wide text-accent-deep uppercase">Label tip</p>
      <button
        type="button"
        onClick={() => setIndex((i) => (i + 1) % tips.length)}
        className="mt-2 block w-full text-left"
        aria-label="Next tip"
      >
        <div key={index} className="rise min-h-[6.5rem]">
          <p className="font-serif text-xl leading-snug font-medium text-ink">{tip.title}</p>
          <p className="mt-2 text-[15px] leading-relaxed text-muted">{tip.text}</p>
        </div>
      </button>
      <div className="mt-3 flex items-center justify-between gap-3 text-xs text-faint">
        <a href={tip.source_url} target="_blank" rel="noreferrer" className="truncate underline">Source: {domain(tip.source_url)}</a>
        <span className="shrink-0">{index + 1} / {tips.length}</span>
      </div>
    </Card>
  )
}
