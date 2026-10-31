import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Check, Plus, type LucideIcon } from 'lucide-react'
import { api, ApiError, type ProductSummary } from '../api'
import { Chip, ErrorNote, Page, ProductImage, Spinner, productTitle, shortBrand } from '../ui'

const STATUS: Record<ProductSummary['status'], { label: string; icon?: LucideIcon; tone: 'accent' | 'warn' | 'good' | 'neutral'; hint?: string }> = {
  needs_ingredients: { label: 'Needs ingredient list', icon: Plus, tone: 'accent', hint: 'Have the pack? Add its ingredients.' },
  data_looks_wrong: { label: 'Data looks wrong', tone: 'warn', hint: 'Have the pack? Help correct it.' },
  checked: { label: 'Checked against the label', icon: Check, tone: 'good' },
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
    <Page wide>
      <div className="rise text-center">
        <span className="eyebrow">Catalog</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">Products we know</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
          Everything people have scanned so far.
          {needHelp > 0 && ` ${needHelp} ${needHelp === 1 ? 'needs' : 'need'} help from someone who has the pack.`}
        </p>
      </div>

      <div className="rise mx-auto mt-6 max-w-xl space-y-3">
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

      <ul className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 lg:grid-cols-4">
        {shown.map((p) => {
          const status = STATUS[p.status]
          return (
            <li key={p.key} className="rise">
              <Link
                to={`/p/${encodeURIComponent(p.key)}`}
                className={`flex h-full flex-col overflow-hidden rounded-3xl border bg-white shadow-card transition-transform hover:-translate-y-0.5 ${p.needs_help ? 'border-accent/50 ring-2 ring-accent/20' : 'border-line'}`}
              >
                <ProductImage
                  productKey={p.key}
                  hasImage={p.has_image}
                  version={p.image_version}
                  seed={shortBrand(p.brand) || productTitle(p)}
                  label={shortBrand(p.brand) || 'Product'}
                  className="aspect-[4/3] rounded-none"
                />
                <div className="flex flex-1 flex-col gap-2 p-3">
                  <p className="line-clamp-2 text-[15px] leading-snug font-medium">{productTitle(p)}</p>
                  <div className="mt-auto space-y-1">
                    <Chip tone={status.tone}>{status.icon && <status.icon className="size-3.5" aria-hidden />}{status.label}</Chip>
                    {status.hint && <p className="text-xs leading-snug text-muted">{status.hint}</p>}
                  </div>
                </div>
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
