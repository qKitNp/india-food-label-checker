import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, ApiError, type Nutrition, type Product } from '../api'
import { toJpeg, UnreadablePhoto } from '../images'
import { Button, Card, ErrorNote, Page, SectionTitle, Spinner } from '../ui'

const FIELDS: [keyof Nutrition, string][] = [
  ['energy_kcal', 'Energy (kcal)'],
  ['sugars_g', 'Sugars (g)'],
  ['added_sugars_g', 'Added sugars (g)'],
  ['fat_g', 'Fat (g)'],
  ['saturated_fat_g', 'Saturated fat (g)'],
  ['carbs_g', 'Carbohydrates (g)'],
  ['protein_g', 'Protein (g)'],
  ['sodium_mg', 'Sodium (mg)'],
]

const input =
  'w-full rounded-xl border border-line bg-white px-3 py-2 text-[15px] outline-none placeholder:text-faint focus:border-accent'

export default function LabelPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [stage, setStage] = useState<'pick' | 'reading' | 'review' | 'saving'>('pick')
  const [product, setProduct] = useState<Product | null>(null)
  const [legible, setLegible] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function read(files: FileList) {
    setStage('reading')
    setError(null)
    try {
      const photos = await Promise.all([...files].slice(0, 4).map((f) => toJpeg(f)))
      const result = await api.extractLabel(photos, params.get('barcode') ?? undefined)
      setProduct({ ...result.product, barcode: result.product.barcode ?? params.get('barcode') })
      setLegible(result.legible)
      setStage('review')
    } catch (e) {
      setError(e instanceof ApiError || e instanceof UnreadablePhoto ? e.message : "Couldn't read the photo. Try a sharper, closer one.")
      setStage('pick')
    }
  }

  async function save() {
    if (!product) return
    setStage('saving')
    setError(null)
    try {
      const verdict = await api.confirm(product)
      navigate(`/p/${encodeURIComponent(verdict.product.key)}`, { replace: true, state: { verdict } })
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Saving failed. Try again.')
      setStage('review')
    }
  }

  const set = (patch: Partial<Product>) => setProduct((p) => (p ? { ...p, ...patch } : p))
  const setNutrient = (k: keyof Nutrition, v: string) =>
    setProduct((p) => (p ? { ...p, nutrition: { ...p.nutrition, [k]: v === '' ? null : Number(v) } } : p))

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">Back label</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">
          {stage === 'review' || stage === 'saving' ? 'Check what we read' : 'Photograph the back of the pack'}
        </h1>
        <p className="mx-auto mt-2 max-w-sm text-sm text-muted">
          {stage === 'review' || stage === 'saving'
            ? 'Fix anything that looks wrong, then save. Your confirmed label becomes the trusted record for this product.'
            : 'Get the ingredients list and the nutrition table in frame. You can add up to four photos.'}
        </p>
      </div>

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}

      {stage === 'pick' && (
        <label className="rise mt-6 flex cursor-pointer flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed border-line-strong bg-white px-6 py-14 text-center hover:bg-wash">
          <span className="rounded-full bg-accent px-5 py-2.5 text-[15px] font-medium text-white shadow-button">Take or choose photos</span>
          <span className="text-xs text-faint">Ingredients and nutrition table</span>
          <input type="file" accept="image/*" multiple className="sr-only" onChange={(e) => e.target.files?.length && read(e.target.files)} />
        </label>
      )}

      {stage === 'reading' && (
        <div className="flex items-center justify-center gap-3 py-20 text-muted">
          <Spinner className="text-accent" /> Reading the label…
        </div>
      )}

      {product && (stage === 'review' || stage === 'saving') && (
        <form
          className="rise mt-6 space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            save()
          }}
        >
          {!legible && <ErrorNote>Parts of the label were hard to read. Check the fields carefully.</ErrorNote>}

          <Card>
            <SectionTitle>Product</SectionTitle>
            <div className="grid gap-3">
              <Field label="Brand"><input className={input} value={product.brand ?? ''} onChange={(e) => set({ brand: e.target.value || null })} /></Field>
              <Field label="Name and variant"><input className={input} value={product.name ?? ''} onChange={(e) => set({ name: e.target.value || null })} /></Field>
              <Field label="Barcode number (needed to save)">
                <input className={input} inputMode="numeric" required value={product.barcode ?? ''} onChange={(e) => set({ barcode: e.target.value.replace(/\D/g, '') || null })} />
              </Field>
              <label className="flex items-center gap-2 text-sm text-ink-2">
                <input type="checkbox" checked={!!product.is_liquid} onChange={(e) => set({ is_liquid: e.target.checked })} className="size-4 accent-accent" />
                It's a drink (values per 100 ml)
              </label>
            </div>
          </Card>

          <Card>
            <SectionTitle>Ingredients</SectionTitle>
            <textarea className={`${input} min-h-32`} value={product.ingredients_text ?? ''} onChange={(e) => set({ ingredients_text: e.target.value || null })} />
          </Card>

          <Card>
            <SectionTitle hint={product.is_liquid ? 'per 100 ml' : 'per 100 g'}>Nutrition</SectionTitle>
            <div className="grid grid-cols-2 gap-3">
              {FIELDS.map(([k, label]) => (
                <Field key={k} label={label}>
                  <input className={input} inputMode="decimal" value={product.nutrition[k] ?? ''} onChange={(e) => setNutrient(k, e.target.value)} />
                </Field>
              ))}
            </div>
          </Card>

          <Card>
            <SectionTitle hint="comma separated">Claims on the pack</SectionTitle>
            <input
              className={input}
              placeholder="e.g. Zero Sugar, No Added Preservatives"
              value={product.claims.join(', ')}
              onChange={(e) => set({ claims: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })}
            />
          </Card>

          <div className="flex gap-3">
            <Button variant="secondary" onClick={() => setStage('pick')} disabled={stage === 'saving'}>Retake</Button>
            <button type="submit" className="flex-1 rounded-full bg-accent px-5 py-2.5 text-[15px] font-medium text-white shadow-button hover:bg-accent-deep disabled:opacity-50" disabled={stage === 'saving'}>
              {stage === 'saving' ? 'Saving…' : 'Save and check'}
            </button>
          </div>
        </form>
      )}
    </Page>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-faint">{label}</span>
      {children}
    </label>
  )
}
