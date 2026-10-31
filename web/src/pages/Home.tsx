import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Camera, Phone } from 'lucide-react'
import { api, type ProductSummary } from '../api'
import { loadRecent } from '../history'
import { Page, ProductImage, productTitle, shortBrand } from '../ui'

const STEPS = [
  ['Point at a pack', 'Scan the barcode, snap the front, or just hold it up to the camera and ask out loud.'],
  ['We find the label', 'From our catalog, Open Food Facts, or the brand and retailer pages, quoted word for word.'],
  ['Read the fine print', '"Zero sugar" claims against the legal cut-off, and additives restricted in the EU or US.'],
]

export default function Home() {
  const navigate = useNavigate()
  const [recent] = useState(loadRecent)
  const [catalog, setCatalog] = useState<ProductSummary[]>([])
  const [code, setCode] = useState('')

  useEffect(() => {
    if (recent.length) return
    api.products().then((ps) => setCatalog(ps.filter((p) => p.has_image && p.source !== 'off' && p.status === 'ok').slice(0, 6))).catch(() => {})
  }, [recent.length])

  const shelf = recent.length
    ? recent.slice(0, 6).map((r) => ({ key: r.key, title: r.title, brand: r.brand }))
    : catalog.map((p) => ({ key: p.key, title: productTitle(p), brand: shortBrand(p.brand) }))

  return (
    <Page bare>
      <section className="relative overflow-hidden border-b border-line">
        {/* Ruled-paper geometry: a compass arc, corner nodes and stepped blocks. */}
        <svg className="pointer-events-none absolute -top-[22rem] -left-[22rem] size-[44rem] text-line-strong" viewBox="0 0 100 100" aria-hidden="true">
          <circle cx="50" cy="50" r="49.9" fill="none" stroke="currentColor" strokeWidth="0.12" />
        </svg>
        <span className="absolute bottom-0 left-0 hidden size-1.5 -translate-x-1/2 translate-y-1/2 bg-ink sm:block" />
        <span className="absolute right-0 bottom-0 hidden size-1.5 translate-x-1/2 translate-y-1/2 bg-ink sm:block" />
        <div className="pointer-events-none absolute bottom-0 left-0 hidden items-end sm:flex" aria-hidden="true">
          <div className="flex flex-col">
            <div className="h-20 w-20 bg-accent-wash" />
            <div className="h-24 w-20 bg-accent" />
          </div>
          <div className="h-14 w-12 bg-accent-wash" />
        </div>
        <div className="pointer-events-none absolute right-0 bottom-0 hidden flex-col items-end sm:flex" aria-hidden="true">
          <div className="mr-3 mb-3 size-14 rotate-45 bg-accent-deep" />
          <div className="h-24 w-20 bg-accent" />
        </div>

        <div className="relative px-5 pt-20 pb-24 text-center sm:pt-28 sm:pb-32">
          <span className="eyebrow">A food label checker for India</span>
          <h1 className="mx-auto mt-6 max-w-4xl text-[clamp(2.6rem,8.5vw,6.25rem)] leading-[0.95] font-semibold tracking-[-0.045em] text-ink">
            Read the <span className="text-accent">fine print</span> on every snack.
          </h1>
          <p className="mx-auto mt-7 max-w-lg text-[17px] leading-relaxed text-muted">
            Point your camera at a packet. See which additives are banned abroad, whether its claims hold up, and how
            the Indian recipe differs from the European one.
          </p>
          <div className="mx-auto mt-9 flex max-w-xs flex-col justify-center gap-3 sm:max-w-none sm:flex-row">
            <Link to="/scan" className="inline-flex items-center justify-center gap-2 bg-ink px-6 py-3.5 text-[15px] font-medium text-white transition-colors hover:bg-ink-2">
              <Camera className="size-4" aria-hidden /> Scan a product
            </Link>
            <Link to="/live" className="inline-flex items-center justify-center gap-2 border border-line-strong bg-white px-6 py-3.5 text-[15px] font-medium text-ink transition-colors hover:bg-wash">
              <Phone className="size-4" aria-hidden /> Ask it live
            </Link>
          </div>
          <form
            className="mx-auto mt-10 flex max-w-sm items-center border border-line bg-white/70 shadow-card focus-within:border-accent"
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
              placeholder="Or type a barcode number"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="min-w-0 flex-1 bg-transparent px-4 py-3 text-[15px] outline-none placeholder:text-faint"
            />
            <button
              type="submit"
              aria-label="Check barcode"
              disabled={code.replace(/\D/g, '').length < 8}
              className="flex self-stretch items-center border-l border-line px-4 text-ink-2 transition-colors hover:bg-wash disabled:opacity-40"
            >
              <ArrowRight className="size-4" aria-hidden />
            </button>
          </form>
        </div>
      </section>

      <section className="grid border-b border-line sm:grid-cols-3">
        {STEPS.map(([title, text], i) => (
          <div key={title} className="border-b border-line px-6 py-10 last:border-b-0 sm:border-r sm:border-b-0 sm:last:border-r-0">
            <p className="font-mono text-xs text-faint">0{i + 1}</p>
            <h2 className="mt-8 text-[1.4rem] leading-tight font-semibold tracking-tight">{title}</h2>
            <p className="mt-2 text-[15px] leading-relaxed text-muted">{text}</p>
          </div>
        ))}
      </section>

      {shelf.length > 0 && (
        <section className="border-b border-line">
          <div className="flex items-baseline justify-between border-b border-line px-6 py-4">
            <h2 className="text-[15px] font-semibold tracking-tight">{recent.length ? 'Recently checked' : 'In the catalog'}</h2>
            <Link to="/products" className="inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
              All products <ArrowRight className="size-3.5" aria-hidden />
            </Link>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
            {shelf.map((p) => (
              <Link
                key={p.key}
                to={`/p/${encodeURIComponent(p.key)}`}
                className="group border-r border-b border-line p-4 transition-colors hover:bg-white [&:nth-child(2n)]:border-r-0 sm:[&:nth-child(2n)]:border-r sm:[&:nth-child(3n)]:border-r-0 lg:[&:nth-child(3n)]:border-r lg:[&:nth-child(6n)]:border-r-0 lg:border-b-0"
              >
                <ProductImage productKey={p.key} seed={p.brand || p.title} label={p.brand || 'Product'} className="aspect-square rounded-xl" />
                <p className="mt-3 line-clamp-2 text-sm leading-snug font-medium text-ink">{p.title}</p>
              </Link>
            ))}
          </div>
        </section>
      )}

      <p className="px-6 py-8 text-center text-xs leading-relaxed text-faint">
        Not medical or legal advice. Not affiliated with FSSAI or any brand. A flag describes a difference in
        regulations between countries, not that a product is unsafe. Product data includes{' '}
        <a className="underline" href="https://world.openfoodfacts.org/" target="_blank" rel="noreferrer">Open Food Facts</a> (ODbL).
      </p>
    </Page>
  )
}

export function CameraIcon() {
  return <Camera className="size-[18px]" aria-hidden />
}
