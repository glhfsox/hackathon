import { useState, type FormEvent } from 'react'
import { parseRange, RANGES, type TimeRange } from '../ranges'

export default function TimeFilter({ range, onChange }: { range: TimeRange; onChange: (range: TimeRange) => void }) {
  const [draft, setDraft] = useState(range.label)
  const [error, setError] = useState('')

  const apply = (event: FormEvent) => {
    event.preventDefault()
    const parsed = parseRange(draft)
    if (!parsed) {
      setError('Use a positive whole number with m, h, or d, such as 45m or 2h.')
      return
    }
    onChange(parsed)
    setDraft(parsed.label)
    setError('')
  }

  return (
    <form className="time-filter" onSubmit={apply} aria-label="Time window">
      <span className="dim">Time window</span>
      <div className="time-presets" role="group" aria-label="Time presets">
        {Object.entries(RANGES).map(([label, minutes]) => (
          <button key={label} type="button" className="btn time-preset" aria-pressed={range.minutes === minutes}
            onClick={() => { onChange({ minutes, label }); setDraft(label); setError('') }}>
            {label}
          </button>
        ))}
      </div>
      <label className="custom-window">
        <span className="dim">Custom</span>
        <input className="field" aria-label="Custom time window" aria-invalid={Boolean(error)} aria-describedby={error ? 'time-window-error' : 'time-window-hint'}
          value={draft} onChange={(event) => { setDraft(event.target.value); setError('') }} placeholder="45m or 2h" size={10} />
      </label>
      <button className="btn apply-window" type="submit">Apply</button>
      {error ? <span id="time-window-error" role="alert" className="error-text">{error}</span>
        : <span id="time-window-hint" className="dim">Last {range.label}</span>}
    </form>
  )
}
