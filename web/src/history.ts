// Recently checked products, kept in this browser only.

export interface Recent {
  key: string
  title: string
  brand: string
  flagged: number
  checkedAt: number
}

const KEY = 'label-check:recent'
const LIMIT = 12

export function loadRecent(): Recent[] {
  try {
    const items = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(items) ? items : []
  } catch {
    return []
  }
}

export function remember(item: Omit<Recent, 'checkedAt'>) {
  try {
    const rest = loadRecent().filter((r) => r.key !== item.key)
    localStorage.setItem(KEY, JSON.stringify([{ ...item, checkedAt: Date.now() }, ...rest].slice(0, LIMIT)))
  } catch {
    /* storage unavailable (private mode); history is a convenience */
  }
}
