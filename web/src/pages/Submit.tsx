import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { Check, X } from 'lucide-react'
import { api, ApiError, type Submission } from '../api'
import { barcodesIn } from '../barcode'
import { toJpeg, UnreadablePhoto } from '../images'
import { Card, ErrorNote, Page, Spinner, buttonClass } from '../ui'

const MAX_PHOTOS = 4
// Barcodes are small: send them sharper than the usual 1600 px.
const PHOTO_SIDE = 2400

interface Picked {
  file: File
  url: string
  barcodes: string[]
}

export default function SubmitPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const key = params.get('key') ?? ''
  const kind = params.get('kind') === 'challenge' ? 'challenge' : 'contribute'
  const [photos, setPhotos] = useState<Picked[]>([])
  const [checking, setChecking] = useState(false)
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Submission | null>(null)

  async function pick(files: FileList) {
    setError(null)
    setChecking(true)
    const added = await Promise.all(
      [...files].slice(0, MAX_PHOTOS - photos.length).map(async (file) => ({
        file,
        url: URL.createObjectURL(file),
        barcodes: await barcodesIn(file),
      })),
    )
    setPhotos((p) => [...p, ...added].slice(0, MAX_PHOTOS))
    setChecking(false)
  }

  async function send() {
    setSending(true)
    setError(null)
    try {
      const blobs = await Promise.all(photos.map((p) => toJpeg(p.file, PHOTO_SIDE)))
      const result = await api.submit(key, kind, blobs, note.trim() || undefined)
      if (result.verdict) {
        navigate(`/p/${encodeURIComponent(result.verdict.product.key)}`, { replace: true, state: { verdict: result.verdict } })
      } else {
        setDone(result.submission)
      }
    } catch (e) {
      setError(e instanceof ApiError || e instanceof UnreadablePhoto ? e.message : 'Sending failed. Try again.')
      setSending(false)
    }
  }

  const barcode = photos.flatMap((p) => p.barcodes)[0]

  if (done) return <ThankYou submission={done} />

  return (
    <Page>
      <div className="rise text-center">
        <span className="eyebrow">{kind === 'challenge' ? 'Challenge' : 'Contribute'}</span>
        <h1 className="mt-3 font-serif text-[1.75rem] leading-tight font-medium tracking-tight">
          {kind === 'challenge' ? 'Is our ingredient list wrong?' : 'Add the ingredient list'}
        </h1>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
          {kind === 'challenge'
            ? "Send photos of the pack you have. We'll compare them with our list and review the difference."
            : "Photograph the back of the pack and we'll read the ingredients from it."}
        </p>
      </div>

      <Card className="rise mt-6">
        <ol className="space-y-2 text-[15px] text-ink-2">
          <li className="flex gap-2.5"><Step n={1} /> The ingredients list, readable and in focus</li>
          <li className="flex gap-2.5"><Step n={2} /> The barcode, visible in one of the photos</li>
        </ol>
        <p className="mt-3 text-xs leading-relaxed text-faint">
          We read the barcode from your photo to make sure it's the same product. Up to {MAX_PHOTOS} photos.
        </p>
      </Card>

      {photos.length > 0 && (
        <div className="rise mt-4 grid grid-cols-4 gap-2">
          {photos.map((p, i) => (
            <div key={i} className="relative aspect-square overflow-hidden rounded-2xl border border-line bg-wash">
              <img src={p.url} alt={`Photo ${i + 1}`} className="size-full object-cover" />
              <button
                type="button"
                aria-label="Remove photo"
                onClick={() => setPhotos((all) => all.filter((_, j) => j !== i))}
                className="absolute top-1 right-1 flex size-6 items-center justify-center rounded-full bg-ink/60 text-xs text-white"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            </div>
          ))}
        </div>
      )}

      {photos.length > 0 && !checking && (
        <p className={`mt-3 text-sm ${barcode ? 'text-good' : 'text-warn'}`}>
          {barcode ? <span className="inline-flex items-center gap-1.5"><Check className="size-4" aria-hidden /> Barcode found: {barcode}</span> : 'No barcode found yet. Add a photo where the barcode is clear.'}
        </p>
      )}
      {checking && <p className="mt-3 flex items-center gap-2 text-sm text-muted"><Spinner className="text-accent" /> Looking for the barcode…</p>}

      {photos.length < MAX_PHOTOS && (
        <label className={buttonClass('secondary', 'mt-4 w-full cursor-pointer py-3')}>
          {photos.length ? 'Add another photo' : 'Take or choose photos'}
          <input type="file" accept="image/*" multiple className="sr-only" onChange={(e) => e.target.files?.length && pick(e.target.files)} />
        </label>
      )}

      {kind === 'challenge' && (
        <label className="mt-4 block">
          <span className="mb-1 block text-xs text-faint">What's different? (optional)</span>
          <textarea
            value={note}
            maxLength={1000}
            onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. My pack has no colour 102"
            className="min-h-24 w-full rounded-xl border border-line bg-white px-3 py-2 text-[15px] outline-none placeholder:text-faint focus:border-accent"
          />
        </label>
      )}

      {error && <div className="mt-4"><ErrorNote>{error}</ErrorNote></div>}

      <button
        type="button"
        onClick={send}
        disabled={!photos.length || sending || checking}
        className={buttonClass('primary', 'mt-5 w-full py-3')}
      >
        {sending ? <><Spinner /> Reading your photos…</> : kind === 'challenge' ? 'Send for review' : 'Add ingredient list'}
      </button>
      <Link to={`/p/${encodeURIComponent(key)}`} className={buttonClass('ghost', 'mt-2 w-full')}>Cancel</Link>
    </Page>
  )
}

function Step({ n }: { n: number }) {
  return (
    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-wash text-xs font-semibold text-accent-deep">
      {n}
    </span>
  )
}

function ThankYou({ submission }: { submission: Submission }) {
  const changes = submission.added_codes.length + submission.removed_codes.length
  return (
    <Page>
      <div className="rise pt-8 text-center">
        <span className="eyebrow">Thank you</span>
        <h1 className="mt-3 font-serif text-[1.75rem] font-medium tracking-tight">We'll review it</h1>
        <p className="mx-auto mt-2 max-w-sm text-sm leading-relaxed text-muted">
          Your photos matched the barcode {submission.barcode}. We keep the current list until someone has checked the
          difference, so one photo can't change it for everyone.
        </p>
      </div>
      <Card className="rise mt-6">
        <p className="font-serif text-lg font-medium">What your photos show</p>
        {changes === 0 ? (
          <p className="mt-2 text-sm text-muted">The same additives as our list. The wording may still differ.</p>
        ) : (
          <div className="mt-2 space-y-1 text-sm text-ink-2">
            {submission.added_codes.length > 0 && <p>Not in our list: <span className="font-medium">{submission.added_codes.join(', ')}</span></p>}
            {submission.removed_codes.length > 0 && <p>In our list but not on your pack: <span className="font-medium">{submission.removed_codes.join(', ')}</span></p>}
          </div>
        )}
        {submission.proposed?.ingredients_text && (
          <details className="mt-3">
            <summary className="cursor-pointer text-xs text-faint">What we read from your photos</summary>
            <p className="mt-2 text-sm leading-relaxed text-muted">{submission.proposed.ingredients_text}</p>
          </details>
        )}
      </Card>
      <Link to={`/p/${encodeURIComponent(submission.product_key)}`} className={buttonClass('secondary', 'mt-6 w-full')}>
        Back to the product
      </Link>
    </Page>
  )
}
