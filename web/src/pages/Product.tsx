import { useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { api, ApiError, type AdditiveFinding, type ClaimFinding, type Nutrition, type Region, type RegionStatus, type Verdict } from '../api'
import { remember } from '../history'
import { Button, Card, Chip, Cover, ErrorNote, Page, SectionTitle, Spinner, buttonClass, productTitle, shortBrand } from '../ui'

const UNRELIABLE = "This product's online data looks unreliable"

function domain(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

export default function ProductPage() {
  const { key = '' } = useParams()
  const location = useLocation()
  const initial = (location.state as { verdict?: Verdict } | null)?.verdict ?? null
  const [verdict, setVerdict] = useState<Verdict | null>(initial)
  const [error, setError] = useState<{ status: number; message: string } | null>(null)

  useEffect(() => {
    if (initial && initial.product.key === key) return
    let cancelled = false
    setVerdict(null)
    setError(null)
    api.product(key).then(
      (v) => !cancelled && setVerdict(v),
      (e) => !cancelled && setError({ status: e instanceof ApiError ? e.status : 0, message: e.message }),
    )
    return () => {
      cancelled = true
    }
  }, [key, initial])

  useEffect(() => {
    if (!verdict) return
    const p = verdict.product
    remember({
      key: p.key,
      title: productTitle(p),
      brand: shortBrand(p.brand),
      flagged: verdict.claims.filter((c) => c.passes === false).length + verdict.additives.filter((a) => a.flagged).length,
    })
  }, [verdict])

  if (error?.status === 404) return <UnknownProduct barcode={key} />
  if (error) return <Page><ErrorNote>{error.message}</ErrorNote></Page>
  if (!verdict) {
    return (
      <Page>
        <div className="flex items-center justify-center gap-3 py-24 text-muted"><Spinner className="text-accent" /> Looking it up…</div>
      </Page>
    )
  }
  return <VerdictView verdict={verdict} onUpdate={setVerdict} />
}

function VerdictView({ verdict, onUpdate }: { verdict: Verdict; onUpdate: (v: Verdict) => void }) {
  const p = verdict.product
  const unreliable = verdict.headline.some((h) => h.startsWith(UNRELIABLE))
  const labelLink = `/label?${new URLSearchParams({ ...(p.barcode ? { barcode: p.barcode } : {}), key: p.key })}`

  return (
    <Page>
      <header className="rise flex items-end gap-4">
        <Cover seed={shortBrand(p.brand) || productTitle(p)} label={shortBrand(p.brand) || 'Product'} className="h-28 w-22 shrink-0 shadow-card" />
        <div className="min-w-0 pb-1">
          {p.brand && <p className="text-sm text-muted">{shortBrand(p.brand)}</p>}
          <h1 className="font-serif text-[1.6rem] leading-tight font-medium tracking-tight">{productTitle(p)}</h1>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <SourceChip verdict={verdict} />
            {p.barcode && <Chip>{p.barcode}</Chip>}
          </div>
        </div>
      </header>

      {unreliable ? (
        <Card className="rise mt-6 border-warn/30 bg-warn-wash/40">
          <p className="font-serif text-lg font-medium">We're not sure about this data</p>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            The online listing for this product contradicts itself
            {verdict.data_issues[0] ? ` (${verdict.data_issues[0].replace(/\.$/, '').toLowerCase()})` : ''}, so we won't
            judge it. A photo of the back label fixes that.
          </p>
          <Link to={labelLink} className={buttonClass('primary', 'mt-4')}>Check the back label</Link>
        </Card>
      ) : (
        <Card className="rise mt-6">
          <SectionTitle>What to know</SectionTitle>
          <ul className="space-y-2.5">
            {verdict.headline.map((h) => (
              <li key={h} className="flex gap-2.5 text-[15px] leading-relaxed text-ink-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
                {h}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {!unreliable && verdict.claims.length > 0 && <Claims claims={verdict.claims} liquid={!!p.is_liquid} />}
      {!unreliable && p.additives.length > 0 && <Additives additives={verdict.additives} all={p.additives} />}
      <NutritionCard nutrition={p.nutrition} liquid={!!p.is_liquid} />
      <Ingredients verdict={verdict} />
      <Compare verdict={verdict} onUpdate={onUpdate} />

      {!p.verified && !unreliable && (
        <div className="rise mt-6 text-center">
          <p className="text-sm text-muted">Have the pack? Its label is the most reliable source.</p>
          <Link to={labelLink} className={buttonClass('secondary', 'mt-3')}>Check the back label</Link>
        </div>
      )}
    </Page>
  )
}

function SourceChip({ verdict }: { verdict: Verdict }) {
  const p = verdict.product
  if (p.verified) return <Chip tone="good">✓ Checked against the label</Chip>
  if (p.source === 'web' && p.evidence) return <Chip tone="accent">From {domain(p.evidence.source_url)}</Chip>
  if (p.source === 'off') return <Chip>Open Food Facts</Chip>
  return <Chip>{p.source}</Chip>
}

function Claims({ claims, liquid }: { claims: ClaimFinding[]; liquid: boolean }) {
  const per = liquid ? '100 ml' : '100 g'
  return (
    <Card className="rise mt-4">
      <SectionTitle hint="EU legal limits">Claims on the pack</SectionTitle>
      <div className="space-y-3">
        {claims.map((c) => (
          <div key={c.rule} className="rounded-2xl bg-wash p-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-[15px] font-medium">Says “{c.claim}”</p>
              {c.passes === false ? <Chip tone="bad">Over the limit</Chip> : c.passes ? <Chip tone="good">Within the limit</Chip> : <Chip>Can't check</Chip>}
            </div>
            {c.actual !== null && (
              <div className="mt-3 flex items-end gap-6">
                <Stat label="Actually has" value={`${c.actual}`} unit={`${c.unit} / ${per}`} />
                <Stat label="Allowed up to" value={`${c.threshold}`} unit={`${c.unit} / ${per}`} muted />
              </div>
            )}
            <p className="mt-2 text-sm leading-relaxed text-muted">{c.message}</p>
          </div>
        ))}
      </div>
    </Card>
  )
}

function Stat({ label, value, unit, muted }: { label: string; value: string; unit: string; muted?: boolean }) {
  return (
    <div>
      <p className="text-xs text-faint">{label}</p>
      <p className={`font-serif text-2xl font-medium ${muted ? 'text-faint' : 'text-ink'}`}>
        {value} <span className="font-sans text-xs font-normal text-faint">{unit}</span>
      </p>
    </div>
  )
}

const FLAGS: Record<Region, string> = { IN: '🇮🇳', EU: '🇪🇺', US: '🇺🇸' }

const STATUS: Record<RegionStatus['status'], { mark: string; tone: 'bad' | 'warn' | 'good' | 'neutral'; word: string }> = {
  banned: { mark: '✕', tone: 'bad', word: 'Banned' },
  restricted: { mark: '!', tone: 'warn', word: 'Restricted' },
  allowed: { mark: '✓', tone: 'good', word: 'Allowed' },
  unknown: { mark: '?', tone: 'neutral', word: 'Not confirmed' },
}
const REGION_NAMES: Record<Region, string> = { IN: 'India', EU: 'European Union', US: 'United States' }

function Additives({ additives, all }: { additives: AdditiveFinding[]; all: string[] }) {
  const sorted = [...additives].sort((a, b) => Number(b.flagged) - Number(a.flagged))
  const matched = new Set(additives.map((a) => a.code).filter(Boolean))
  const unreviewed = all.filter((code) => !matched.has(code))
  return (
    <Card className="rise mt-4">
      <SectionTitle hint={`${additives.filter((a) => a.flagged).length} banned somewhere`}>Additives and substances</SectionTitle>
      <ul className="divide-y divide-line">
        {sorted.map((a) => (
          <li key={a.id} className="py-3 first:pt-0">
            <details className="group">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
                <span className="text-[15px] font-medium">
                  {a.name} {a.code && <span className="text-sm font-normal text-faint">{a.code}</span>}
                </span>
                <span className="flex shrink-0 gap-1">
                  {(['IN', 'EU', 'US'] as Region[]).map((r) => (
                    <Chip key={r} tone={STATUS[a.status[r].status].tone} title={`${REGION_NAMES[r]}: ${STATUS[a.status[r].status].word}`}>
                      {FLAGS[r]} {STATUS[a.status[r].status].mark}
                    </Chip>
                  ))}
                </span>
              </summary>
              {a.flagged && <p className="mt-1 text-sm text-muted">{a.message}</p>}
              <div className="mt-3 space-y-2">
                {(['IN', 'EU', 'US'] as Region[]).map((r) => {
                  const s = a.status[r]
                  return (
                    <div key={r} className="rounded-2xl bg-wash px-3 py-2.5 text-sm">
                      <p className="font-medium">
                        {FLAGS[r]} {REGION_NAMES[r]}: {STATUS[s.status].word}
                        {s.since ? ` since ${s.since}` : ''}
                      </p>
                      {s.reason && <p className="mt-0.5 text-muted">{s.reason}</p>}
                      {s.quote && <p className="mt-1 text-xs text-faint italic">“{s.quote.length > 220 ? s.quote.slice(0, 220) + '…' : s.quote}”</p>}
                      {s.source_url ? (
                        <a href={s.source_url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-xs text-accent-deep underline">
                          {domain(s.source_url)}
                        </a>
                      ) : (
                        <p className="mt-0.5 text-xs text-faint">We couldn't find an official source for this yet.</p>
                      )}
                    </div>
                  )
                })}
              </div>
            </details>
          </li>
        ))}
      </ul>
      {unreviewed.length > 0 && (
        <p className={`text-sm leading-relaxed text-muted ${sorted.length ? 'mt-3 border-t border-line pt-3' : ''}`}>
          Also contains, not reviewed yet: <span className="font-medium text-ink-2">{unreviewed.join(', ')}</span>
        </p>
      )}
      {sorted.length > 0 && <p className="mt-3 text-xs text-faint">Tap a substance to see each country's rule and its source.</p>}
    </Card>
  )
}

const NUTRIENTS: [keyof Nutrition, string, string][] = [
  ['energy_kcal', 'Energy', 'kcal'],
  ['sugars_g', 'Sugars', 'g'],
  ['fat_g', 'Fat', 'g'],
  ['saturated_fat_g', 'Saturated fat', 'g'],
  ['carbs_g', 'Carbohydrates', 'g'],
  ['protein_g', 'Protein', 'g'],
  ['sodium_mg', 'Sodium', 'mg'],
]

function NutritionCard({ nutrition, liquid }: { nutrition: Nutrition; liquid: boolean }) {
  const rows = NUTRIENTS.filter(([k]) => nutrition[k] !== null)
  if (!rows.length) return null
  return (
    <Card className="rise mt-4">
      <SectionTitle hint={liquid ? 'per 100 ml' : 'per 100 g'}>Nutrition</SectionTitle>
      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {rows.map(([k, label, unit]) => (
          <div key={k} className="rounded-2xl bg-wash px-3 py-2.5">
            <dt className="text-xs text-faint">{label}</dt>
            <dd className="font-serif text-xl font-medium">
              {nutrition[k]} <span className="font-sans text-xs font-normal text-faint">{unit}</span>
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  )
}

function Ingredients({ verdict }: { verdict: Verdict }) {
  const p = verdict.product
  if (!p.ingredients_text) return null
  return (
    <Card className="rise mt-4">
      <SectionTitle>Ingredients</SectionTitle>
      <p className="text-[15px] leading-relaxed text-ink-2">{p.ingredients_text}</p>
      {p.evidence && (
        <p className="mt-3 text-xs text-faint">
          Quoted word for word from{' '}
          <a href={p.evidence.source_url} target="_blank" rel="noreferrer" className="underline">{domain(p.evidence.source_url)}</a>
          , not read from your pack.
        </p>
      )}
      {p.source === 'off' && (
        <p className="mt-3 text-xs text-faint">
          From <a href="https://world.openfoodfacts.org/" target="_blank" rel="noreferrer" className="underline">Open Food Facts</a> (ODbL), contributed by volunteers.
        </p>
      )}
    </Card>
  )
}

function Compare({ verdict, onUpdate }: { verdict: Verdict; onUpdate: (v: Verdict) => void }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const c = verdict.counterpart

  async function load() {
    setLoading(true)
    setError(null)
    try {
      onUpdate(await api.counterpart(verdict.product.key, 'EU'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Something went wrong.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card className="rise mt-4">
      <SectionTitle>The European version</SectionTitle>
      {c ? (
        <div className="space-y-3 text-[15px]">
          <p className="text-ink-2">
            Compared with <span className="font-medium">{c.product_name}</span> sold in {c.country}.
          </p>
          {c.only_in_india.length + c.only_in_counterpart.length === 0 ? (
            <p className="text-muted">Same additives in both versions.</p>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <Diff title="Only in the Indian pack" codes={c.only_in_india} />
              <Diff title={`Only in the ${c.country} pack`} codes={c.only_in_counterpart} />
            </div>
          )}
          <p className="text-xs text-faint">
            Ingredients quoted from <a href={c.source_url} target="_blank" rel="noreferrer" className="underline">{domain(c.source_url)}</a>.
          </p>
        </div>
      ) : (
        <>
          <p className="text-[15px] leading-relaxed text-muted">
            Some products sold in India use different ingredients from the same brand's product in Europe. We can look it up.
          </p>
          {error && <div className="mt-3"><ErrorNote>{error}</ErrorNote></div>}
          <Button variant="secondary" className="mt-4" onClick={load} disabled={loading}>
            {loading ? <><Spinner className="text-accent" /> Searching… this can take a minute</> : 'Compare with the EU version'}
          </Button>
        </>
      )}
    </Card>
  )
}

function Diff({ title, codes }: { title: string; codes: string[] }) {
  return (
    <div className="rounded-2xl bg-wash p-3">
      <p className="text-xs text-faint">{title}</p>
      <p className="mt-1 text-sm font-medium">{codes.length ? codes.join(', ') : 'None'}</p>
    </div>
  )
}

function UnknownProduct({ barcode }: { barcode: string }) {
  return (
    <Page>
      <div className="rise pt-10 text-center">
        <span className="eyebrow">Not found</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">We don't know this one yet</h1>
        <p className="mx-auto mt-3 max-w-sm text-[15px] text-muted">
          Barcode {barcode} isn't in our catalog or Open Food Facts. Snap the front of the pack and we'll search for it, or
          photograph the back label.
        </p>
        <div className="mt-6 flex flex-col items-center gap-3">
          <Link to="/scan" className={buttonClass('primary')}>Snap the front of the pack</Link>
          <Link to={`/label?barcode=${encodeURIComponent(barcode)}`} className={buttonClass('secondary')}>Photograph the back label</Link>
        </div>
      </div>
    </Page>
  )
}
