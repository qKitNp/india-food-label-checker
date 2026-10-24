import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, type Job, type Step } from '../api'
import { Card, ErrorNote, Page, Spinner, buttonClass } from '../ui'

const POLL_MS = 1500

function domain(url: unknown) {
  try {
    return new URL(String(url)).hostname.replace(/^www\./, '')
  } catch {
    return 'a page'
  }
}

/** Turn an agent step into a plain sentence. */
function describe(step: Step): { text: string; tone?: 'good' | 'bad' } {
  const a = step.args
  switch (step.tool) {
    case 'read_pack': {
      try {
        const ident = JSON.parse(step.result)
        const name = [ident.brand, ident.product_name].filter(Boolean).join(' ')
        return { text: name ? `Read the pack: ${name}` : 'Read the pack' }
      } catch {
        return { text: 'Read the pack' }
      }
    }
    case 'search_catalog':
      return { text: a.with_photo ? 'Compared it with products we know, by name and pack photo' : 'Searched products we know' }
    case 'get_catalog_product':
      return { text: 'Looked closely at a possible match' }
    case 'use_catalog_product':
      return { text: 'Matched a product we already know', tone: 'good' }
    case 'web_search':
      return { text: `Searched the web for "${String(a.query ?? '')}"` }
    case 'read_page':
      return { text: `Read ${domain(a.url)}` }
    case 'submit_product':
      return step.result.startsWith('Accepted')
        ? { text: `Found the ingredients on ${domain(a.source_url)} and checked the quote`, tone: 'good' }
        : { text: `Rejected a source: ${step.result.replace(/^Rejected:\s*/, '')}`, tone: 'bad' }
    case 'give_up':
      return { text: "Couldn't find reliable ingredients", tone: 'bad' }
    default:
      return { text: step.tool }
  }
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

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">Checking</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">
          {finished ? 'Done looking' : 'Finding the ingredients'}
        </h1>
        {!finished && <p className="mt-2 text-sm text-muted">New products can take up to a minute while we search the web.</p>}
      </div>

      <Card className="rise mt-6">
        <ol className="space-y-3">
          {(job?.steps ?? []).map((step, i) => {
            const { text, tone } = describe(step)
            return (
              <li key={i} className="rise flex gap-3 text-[15px]">
                <span
                  className={`mt-1.5 size-2 shrink-0 rounded-full ${tone === 'bad' ? 'bg-bad' : tone === 'good' ? 'bg-good' : 'bg-accent'}`}
                />
                <span className={tone === 'bad' ? 'text-muted' : 'text-ink-2'}>{text}</span>
              </li>
            )
          })}
          {!finished && (
            <li className="flex items-center gap-3 text-[15px] text-faint">
              <Spinner className="text-accent" />
              {job?.steps.length ? 'Working…' : 'Reading the pack…'}
            </li>
          )}
        </ol>
      </Card>

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}

      {job?.status === 'failed' && (
        <div className="mt-4"><ErrorNote>Something went wrong while checking this product. {job.error}</ErrorNote></div>
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
