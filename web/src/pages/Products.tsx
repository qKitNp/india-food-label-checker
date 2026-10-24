import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, type ProductSummary } from '../api'
import { Chip, Cover, ErrorNote, Page, Spinner, productTitle, shortBrand } from '../ui'

const STATUS: Record<ProductSummary['status'], { label: string; tone: 'accent' | 'warn' | 'good' | 'neutral'; hint?: string }> = {
  needs_ingredients: { label: '+ Needs ingredient list', tone: 'accent', hint: 'Have the pack? Add its ingredients.' },
  data_looks_wrong: { label: 'Data looks wrong', tone: 'warn', hint: 'Have the pack? Help correct it.' },
  checked: { label: '✓ Checked against the label', tone: 'good' },
  ok: { label: 'From online sources', tone: 'neutral' },
}

export default function ProductsPage() {
  const [params, setParams] = useSearchParams()
  const helpOnly = params.get('filter') === 'help'
  const [products, setProducts] = useState<ProductSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    api.products().then(setProducts, (e) => setError(e instanceof ApiError ? e.message : 'Could not load products.'))
  }, [])

  const needHelp = products?.filter((p) => p.needs_help).length ?? 0
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (products ?? []).filter(
      (p) =>
        (!helpOnly || p.needs_help) &&
        (!q || [p.brand, p.name, p.barcode].some((v) => v?.toLowerCase().includes(q))),
    )
  }, [products, helpOnly, query])

  const setFilter = (help: boolean) => setParams(help ? { filter: 'help' } : {}, { replace: true })

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">Catalog</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">Products we know</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
          Everything people have scanned so far.
          {needHelp > 0 && ` ${needHelp} need help from someone who has the pack.`}
        </p>
      </div>

      <div className="rise mt-6 space-y-3">
        <label htmlFor="search" className="sr-only">Search products</label>
        <input
          id="search"
          type="search"
          placeholder="Search by brand, name or barcode"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full rounded-full border border-line bg-white px-4 py-2.5 text-[15px] shadow-card outline-none placeholder:text-faint focus:border-accent"
        />
        <div className="flex gap-2" role="group" aria-label="Filter">
          <FilterButton active={!helpOnly} onClick={() => setFilter(false)}>All</FilterButton>
          <FilterButton active={helpOnly} onClick={() => setFilter(true)}>
            Needs your help{needHelp > 0 && <span className="ml-1.5 rounded-full bg-accent px-1.5 text-xs text-white">{needHelp}</span>}
          </FilterButton>
        </div>
      </div>

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}
      {!products && !error && (
        <div className="flex items-center justify-center gap-3 py-16 text-muted"><Spinner className="text-accent" /> Loading…</div>
      )}
      {products && shown.length === 0 && (
        <p className="py-12 text-center text-sm text-muted">
          {helpOnly ? 'Nothing needs help right now. Thank you!' : 'No products match your search.'}
        </p>
      )}

      <ul className="mt-5 space-y-3">
        {shown.map((p) => {
          const status = STATUS[p.status]
          return (
            <li key={p.key}>
              <Link
                to={`/p/${encodeURIComponent(p.key)}`}
                className={`rise flex items-center gap-3 rounded-3xl border bg-white p-3 shadow-card hover:bg-wash ${p.needs_help ? 'border-accent/40' : 'border-line'}`}
              >
                <Cover seed={shortBrand(p.brand) || productTitle(p)} label={shortBrand(p.brand) || 'Product'} className="h-16 w-12 shrink-0" />
                <div className="min-w-0 flex-1">
                  {p.brand && <p className="truncate text-xs text-faint">{shortBrand(p.brand)}</p>}
                  <p className="line-clamp-2 text-[15px] leading-snug font-medium">{productTitle(p)}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <Chip tone={status.tone}>{status.label}</Chip>
                    {p.barcode && <span className="text-xs text-faint">{p.barcode}</span>}
                  </div>
                  {status.hint && <p className="mt-1 text-xs text-muted">{status.hint}</p>}
                </div>
                <span aria-hidden="true" className="text-faint">›</span>
              </Link>
            </li>
          )
        })}
      </ul>
    </Page>
  )
}

function FilterButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`inline-flex items-center rounded-full border px-4 py-1.5 text-sm font-medium transition-colors ${active ? 'border-accent bg-accent-wash text-accent-deep' : 'border-line bg-white text-muted hover:bg-wash'}`}
    >
      {children}
    </button>
  )
}
