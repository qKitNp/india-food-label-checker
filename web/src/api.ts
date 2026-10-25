// Client for the FastAPI backend. Types mirror backend/app/models.py, agent.py and jobs.py.

export const API_URL = (import.meta.env.VITE_API_URL ?? 'https://food-label-api.tinkerers.space').replace(/\/$/, '')

export type Region = 'IN' | 'EU' | 'US'

export interface Nutrition {
  energy_kcal: number | null
  carbs_g: number | null
  sugars_g: number | null
  added_sugars_g: number | null
  fat_g: number | null
  saturated_fat_g: number | null
  trans_fat_g: number | null
  protein_g: number | null
  sodium_mg: number | null
}

export interface Product {
  key: string
  barcode: string | null
  name: string | null
  brand: string | null
  ingredients_text: string | null
  additives: string[]
  nutrition: Nutrition
  claims: string[]
  is_liquid: boolean | null
  source: 'label' | 'off' | 'curated' | 'web'
  verified: boolean
  evidence: { source_url: string; ingredients_quote: string } | null
  image_file_id: string | null
  image_url: string | null
}

export interface ClaimFinding {
  claim: string
  rule: string
  actual: number | null
  threshold: number
  unit: string
  passes: boolean | null
  message: string
  source_url: string
  source_verified: boolean
}

/** A substance's status in one region; anything but "unknown" is backed by a verified quote. */
export interface RegionStatus {
  status: 'banned' | 'restricted' | 'allowed' | 'unknown'
  since: string | null
  reason: string | null
  fact: string | null
  quote: string | null
  source_url: string | null
}

export interface AdditiveFinding {
  id: string
  code: string
  name: string
  category: string
  matched: string
  status: Record<Region, RegionStatus>
  flagged: boolean
  message: string
  origin: 'seed' | 'auto' // auto: researched automatically after a scan found it
  researched_at: string | null
}

/** A product number compared with a legal limit. Informational: never flagged. */
export interface LimitCheck {
  name: string
  region: Region
  actual: number
  limit: number
  unit: string
  result: 'within' | 'above'
  message: string
  source_url: string
}

/** One entry of the ingredients list, parsed by the server. */
export interface Ingredient {
  name: string
  percent: string | null
  codes: string[]
  sub: Ingredient[]
  section: string | null
  flagged: boolean // contains a substance banned in at least one country
  flag_reason: string | null
}

/** A code on the label that isn't in our researched list yet. */
export interface OtherAdditive {
  code: string
  name: string | null
  researching: boolean
}

/** A label-reading tip, checked against the official source it links to. */
export interface Tip {
  id: string
  title: string
  text: string
  source_url: string
}

export interface Counterpart {
  region: Region
  country: string
  product_name: string
  ingredients_quote: string
  source_url: string
  additives: string[]
  only_in_india: string[]
  only_in_counterpart: string[]
}

export interface Verdict {
  product: Product
  data_issues: string[]
  claims: ClaimFinding[]
  additives: AdditiveFinding[]
  counterpart: Counterpart | null
  headline: string[]
  other_additives: OtherAdditive[]
  ingredients: Ingredient[]
  limits: LimitCheck[]
}

export interface Step {
  tool: string
  args: Record<string, unknown>
  result: string
}

export interface Identification {
  is_packaged_food: boolean
  brand: string | null
  product_name: string | null
  net_quantity: string | null
  barcode: string | null
}

export interface AgentResult {
  status: 'barcode' | 'catalog' | 'web' | 'not_found' | 'not_food'
  identification: Identification
  product: Product | null
  reason: string
  trace: Step[]
}

export interface Job {
  id: string
  status: 'queued' | 'running' | 'done' | 'failed'
  stage: 'queued' | 'reading' | 'matching' | 'searching' | 'checking' | 'done' | 'failed'
  steps: Step[] // logged for observability; the UI shows `stage` instead
  result: { result: AgentResult; verdict: Verdict | null } | null
  error: string | null
}

export type LiveReason = 'barcode' | 'catalog_image' | 'name' | 'researched' | 'researching' | 'slow' | 'not_found' | 'idle'
  | 'no_frame' | 'stale' | 'unknown' | 'not_food'

export interface LiveLookup {
  found: boolean
  reason: LiveReason
  answer: string
  product_key: string | null
  job_id: string | null
  poll_url: string | null
  snap_at: number | null
}

export interface LiveResultFeed {
  found: boolean
  answer: string | null
  reason: string | null
  product_key: string | null
  job_id: string | null
  poll_url: string | null
  at: number | null
  snap_at: number | null
  looking: boolean
  latest_snap_at: number | null
}

/** The frame a live lookup is working on; `at` busts the cache per snap. */
export const liveSnapUrl = (sessionId: string, at: number) =>
  `${API_URL}/live/snap?session_id=${encodeURIComponent(sessionId)}&at=${at}`

export interface LabelResult {
  product: Product
  legible: boolean
}

/** A shopper's photo of a pack: adds a missing ingredient list or challenges ours. */
export interface Submission {
  id: string
  product_key: string
  kind: 'contribute' | 'challenge'
  status: 'applied' | 'pending_review' | 'accepted' | 'rejected'
  barcode: string
  note: string | null
  current_ingredients: string | null
  proposed: Product | null
  added_codes: string[]
  removed_codes: string[]
}

/** A catalog entry for the product list, with what it needs from shoppers. */
export interface ProductSummary {
  key: string
  barcode: string | null
  brand: string | null
  name: string | null
  source: Product['source']
  verified: boolean
  status: 'needs_ingredients' | 'data_looks_wrong' | 'checked' | 'ok'
  needs_help: boolean
  has_image: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(API_URL + path, init)
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.")
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

function form(images: Blob[], extra: Record<string, string> = {}): FormData {
  const data = new FormData()
  images.forEach((blob, i) => data.append('images', blob, `photo-${i}.jpg`))
  for (const [k, v] of Object.entries(extra)) data.append(k, v)
  return data
}

export const imageUrl = (key: string) => `${API_URL}/products/${encodeURIComponent(key)}/image`

export const api = {
  products: () => request<ProductSummary[]>('/products'),
  product: (key: string) => request<Verdict>(`/products/${encodeURIComponent(key)}`),
  counterpart: (key: string, region: 'EU' | 'US' = 'EU') =>
    request<Verdict>(`/products/${encodeURIComponent(key)}/counterpart?region=${region}`),
  identify: (photo: Blob) => request<{ job_id: string; poll_url: string }>('/identify', { method: 'POST', body: form([photo]) }),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  tips: () => request<Tip[]>('/tips'),
  extractLabel: (photos: Blob[], barcode?: string) =>
    request<LabelResult>('/labels/extract', { method: 'POST', body: form(photos, barcode ? { barcode } : {}) }),
  submit: (key: string, kind: 'contribute' | 'challenge', photos: Blob[], note?: string) =>
    request<{ submission: Submission; verdict: Verdict | null }>(`/products/${encodeURIComponent(key)}/submissions`, {
      method: 'POST',
      body: form(photos, { kind, ...(note ? { note } : {}) }),
    }),
  confirm: (product: Product) =>
    request<Verdict>('/products/confirm', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(product),
    }),
  liveFrame: (sessionId: string, photo: Blob) => {
    const data = new FormData()
    data.append('session_id', sessionId)
    data.append('image', photo, 'frame.jpg')
    return request<{ ok: boolean; session_id: string }>('/live/frame', { method: 'POST', body: data })
  },
  liveLookup: (sessionId: string) =>
    request<LiveLookup>('/voice/live-lookup', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      // "page": we follow background jobs here, so a finished one isn't re-announced.
      body: JSON.stringify({ session_id: sessionId, source: 'page' }),
    }),
  liveResult: (sessionId: string) =>
    request<LiveResultFeed>(`/live/result?session_id=${encodeURIComponent(sessionId)}`),
  liveSession: (sessionId: string) =>
    request<unknown>('/live/session', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ user_data: { session_id: sessionId } }),
    }),
}
