// Time windows of the dashboard filter, in minutes.
export const RANGES = { '15m': 15, '1h': 60, '24h': 1440, '7d': 10080 } as const
export type Range = keyof typeof RANGES

export const sinceIso = (range: Range) => new Date(Date.now() - RANGES[range] * 60_000).toISOString()
