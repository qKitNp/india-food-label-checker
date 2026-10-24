import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Page({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh pb-16">
      <Nav />
      <main className="mx-auto w-full max-w-xl px-4 pt-6">{children}</main>
    </div>
  )
}

function Nav() {
  return (
    <div className="sticky top-0 z-20 px-3 pt-3">
      <nav className="mx-auto flex max-w-xl items-center justify-between rounded-2xl border border-line/70 bg-wash/80 px-3 py-2 backdrop-blur">
        <Link to="/" className="flex items-center gap-2 text-[15px] font-semibold text-accent-deep">
          <Logo />
          Label Check
        </Link>
        <Link
          to="/scan"
          className="rounded-full border border-line bg-white px-3 py-1.5 text-sm font-medium text-ink-2 shadow-card hover:bg-wash"
        >
          Scan
        </Link>
      </nav>
    </div>
  )
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="#0ea5e9" />
      <path d="M9 16.5l4.5 4.5L23 11.5" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

type Variant = 'primary' | 'secondary' | 'ghost'

const buttonStyles: Record<Variant, string> = {
  primary: 'bg-accent text-white shadow-button hover:bg-accent-deep',
  secondary: 'bg-white text-ink border border-line shadow-card hover:bg-wash',
  ghost: 'text-accent-deep hover:bg-accent-wash',
}

export function buttonClass(variant: Variant = 'primary', extra = '') {
  return `inline-flex items-center justify-center gap-2 rounded-full px-5 py-2.5 text-[15px] font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none ${buttonStyles[variant]} ${extra}`
}

export function Button({ variant = 'primary', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return <button type="button" className={buttonClass(variant, className)} {...props} />
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-3xl border border-line bg-white p-5 shadow-card ${className}`}>{children}</section>
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h2 className="font-serif text-xl font-medium text-ink">{children}</h2>
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </div>
  )
}

type Tone = 'neutral' | 'good' | 'warn' | 'bad' | 'accent'

const toneStyles: Record<Tone, string> = {
  neutral: 'border-line bg-white text-muted',
  good: 'border-transparent bg-good-wash text-good',
  warn: 'border-transparent bg-warn-wash text-warn',
  bad: 'border-transparent bg-bad-wash text-bad',
  accent: 'border-transparent bg-accent-wash text-accent-deep',
}

export function Chip({ children, tone = 'neutral', title }: { children: ReactNode; tone?: Tone; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium ${toneStyles[tone]}`}>
      {children}
    </span>
  )
}

export function Spinner({ className = '' }: { className?: string }) {
  return (
    <span
      role="status"
      aria-label="Loading"
      className={`inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent ${className}`}
    />
  )
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return <p className="rounded-2xl bg-bad-wash px-4 py-3 text-sm text-bad">{children}</p>
}

// Vivid covers, like Keet's course cards: the one place the UI uses strong colour.
const COVERS = [
  ['#7c3aed', '#c026d3'],
  ['#dc2626', '#f97316'],
  ['#0284c7', '#06b6d4'],
  ['#c2410c', '#eab308'],
  ['#15803d', '#84cc16'],
  ['#be185d', '#f43f5e'],
  ['#4338ca', '#0ea5e9'],
]

function hash(text: string) {
  let h = 0
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return h
}

export function Cover({ seed, label, className = '' }: { seed: string; label: string; className?: string }) {
  const [a, b] = COVERS[hash(seed) % COVERS.length]
  return (
    <div
      className={`relative overflow-hidden rounded-2xl text-white ${className}`}
      style={{ background: `linear-gradient(140deg, ${a}, ${b})` }}
    >
      <div className="absolute -right-6 -bottom-8 size-28 rounded-full bg-white/15" />
      <div className="absolute -right-2 top-6 size-14 rounded-full bg-white/10" />
      <span className="relative block truncate px-3 pt-2.5 text-[13px] font-semibold">{label}</span>
    </div>
  )
}

/** The brand is shown separately above the title, so the title is just the product name. */
export function productTitle(p: { brand: string | null; name: string | null }) {
  return p.name || p.brand || 'Unnamed product'
}

/** "Kurkure (PepsiCo)" -> "Kurkure": the agent sometimes adds the parent company. */
export function shortBrand(brand: string | null) {
  return (brand ?? '').replace(/\s*\(.*?\)\s*/g, ' ').trim()
}
