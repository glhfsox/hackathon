// Time windows of the dashboard filter, in minutes.
export const RANGES = { '15m': 15, '1h': 60, '24h': 1440, '7d': 10080 } as const
export interface TimeRange {
  minutes: number
  label: string
}

export function parseRange(value: string): TimeRange | null {
  const match = /^(\d+)\s*([mhd])$/i.exec(value.trim())
  if (!match) return null
  const amount = Number(match[1])
  const unit = match[2].toLowerCase()
  const minutes = amount * (unit === 'd' ? 1440 : unit === 'h' ? 60 : 1)
  const since = new Date(Date.now() - minutes * 60_000)
  if (!Number.isSafeInteger(minutes) || minutes <= 0 || !Number.isFinite(since.getTime()) || since.getUTCFullYear() < 1) return null
  return { minutes, label: `${amount}${unit}` }
}

export const sinceIso = (range: TimeRange) => new Date(Date.now() - range.minutes * 60_000).toISOString()
