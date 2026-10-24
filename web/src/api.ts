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

export interface AdditiveStatus {
  allowed: boolean | null
  note: string
  source_url: string | null
}

export interface AdditiveFinding {
  code: string
  name: string
  status: Record<Region, AdditiveStatus>
  flagged: boolean
  message: string
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
  steps: Step[]
  result: { result: AgentResult; verdict: Verdict | null } | null
  error: string | null
}

export interface LabelResult {
  product: Product
  legible: boolean
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

export const api = {
  product: (key: string) => request<Verdict>(`/products/${encodeURIComponent(key)}`),
  counterpart: (key: string, region: 'EU' | 'US' = 'EU') =>
    request<Verdict>(`/products/${encodeURIComponent(key)}/counterpart?region=${region}`),
  identify: (photo: Blob) => request<{ job_id: string; poll_url: string }>('/identify', { method: 'POST', body: form([photo]) }),
  job: (id: string) => request<Job>(`/jobs/${id}`),
  extractLabel: (photos: Blob[], barcode?: string) =>
    request<LabelResult>('/labels/extract', { method: 'POST', body: form(photos, barcode ? { barcode } : {}) }),
  confirm: (product: Product) =>
    request<Verdict>('/products/confirm', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(product),
    }),
}
