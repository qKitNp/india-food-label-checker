import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { loadRecent } from '../history'
import { Card, Cover, Page, buttonClass } from '../ui'

const STEPS = [
  ['Scan', 'Point your camera at the barcode, or snap the front of the pack.'],
  ['We look it up', 'From our catalog, Open Food Facts, or the brand and retailer pages, quoted word for word.'],
  ['See what the label means', '"Zero" claims against the legal cut-off, and additives restricted in the EU or US.'],
]

export default function Home() {
  const navigate = useNavigate()
  const [recent] = useState(loadRecent)
  const [code, setCode] = useState('')

  return (
    <Page>
      <header className="rise pt-8 pb-10 text-center">
        <span className="eyebrow">A food label checker for India</span>
        <h1 className="mx-auto mt-4 max-w-md font-serif text-[2.1rem] leading-[1.15] font-medium tracking-tight text-ink">
          What's really in your snack?
        </h1>
        <p className="mx-auto mt-4 max-w-sm text-[15px] leading-relaxed text-muted">
          Scan a packet to see how its marketing claims compare with the legal limits, and which of its
          additives are restricted in Europe or the US.
        </p>
        <div className="mt-7 flex flex-col items-center gap-3">
          <Link to="/scan" className={buttonClass('primary', 'px-7 py-3 text-base')}>
            <CameraIcon />
            Scan a product
          </Link>
          <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 py-1 text-xs text-muted">
            Works with Indian barcodes and pack photos
          </span>
        </div>
      </header>

      <form
        className="rise mb-10 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          const digits = code.replace(/\D/g, '')
          if (digits.length >= 8) navigate(`/p/${digits}`)
        }}
      >
        <label htmlFor="barcode" className="sr-only">Barcode number</label>
        <input
          id="barcode"
          inputMode="numeric"
          placeholder="Or type the barcode number"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="min-w-0 flex-1 rounded-full border border-line bg-white px-4 py-2.5 text-[15px] shadow-card outline-none placeholder:text-faint focus:border-accent"
        />
        <button type="submit" className={buttonClass('secondary')} disabled={code.replace(/\D/g, '').length < 8}>
          Check
        </button>
      </form>

      {recent.length > 0 && (
        <section className="rise mb-10">
          <h2 className="mb-3 font-serif text-xl font-medium">Recently checked</h2>
          <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-2">
            {recent.map((r) => (
              <Link key={r.key} to={`/p/${encodeURIComponent(r.key)}`} className="w-36 shrink-0 snap-start">
                <Cover seed={r.brand || r.title} label={r.brand || 'Product'} className="h-40 shadow-card" />
                <p className="mt-2 line-clamp-2 text-sm font-medium text-ink">{r.title}</p>
                <p className="text-xs text-faint">{r.flagged ? `${r.flagged} thing${r.flagged > 1 ? 's' : ''} to note` : 'Nothing flagged'}</p>
              </Link>
            ))}
          </div>
        </section>
      )}

      <Card className="rise">
        <h2 className="mb-4 font-serif text-xl font-medium">How it works</h2>
        <ol className="space-y-4">
          {STEPS.map(([title, text], i) => (
            <li key={title} className="flex gap-3">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent-wash text-sm font-semibold text-accent-deep">
                {i + 1}
              </span>
              <div>
                <p className="text-[15px] font-medium">{title}</p>
                <p className="text-sm leading-relaxed text-muted">{text}</p>
              </div>
            </li>
          ))}
        </ol>
      </Card>

      <p className="mt-8 px-2 text-center text-xs leading-relaxed text-faint">
        Not medical or legal advice. Not affiliated with FSSAI or any brand. A flag describes a difference in
        regulations between countries, not that a product is unsafe. Product data includes{' '}
        <a className="underline" href="https://world.openfoodfacts.org/" target="_blank" rel="noreferrer">Open Food Facts</a> (ODbL).
      </p>
    </Page>
  )
}

export function CameraIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
      <circle cx="12" cy="13" r="3.5" />
    </svg>
  )
}
