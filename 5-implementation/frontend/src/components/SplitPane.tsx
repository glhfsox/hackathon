import { useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

interface SplitPaneProps {
  label: string
  direction?: 'horizontal' | 'vertical'
  initial: number
  minFirst?: number
  minSecond?: number
  first: ReactNode
  second: ReactNode
  className?: string
}

/** A shared boundary: pointer capture keeps dragging reliable outside the narrow grip. */
export default function SplitPane({ label, direction = 'horizontal', initial, minFirst = 160, minSecond = 160, first, second, className = '' }: SplitPaneProps) {
  const root = useRef<HTMLDivElement>(null)
  const id = useId()
  const drag = useRef<{ position: number; ratio: number } | null>(null)
  const [ratio, setRatio] = useState(initial)
  const [size, setSize] = useState(0)
  const horizontal = direction === 'horizontal'

  useLayoutEffect(() => {
    const element = root.current!
    const measure = () => setSize(horizontal ? element.clientWidth : element.clientHeight)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [horizontal])

  const available = Math.max(1, size - 8)
  // If the viewport cannot fit both minimums, divide the available space proportionally.
  const scale = Math.min(1, available / (minFirst + minSecond))
  const lower = minFirst * scale / available
  const upper = 1 - minSecond * scale / available
  const clamp = (value: number) => Math.max(lower, Math.min(upper, value))
  const current = size ? clamp(ratio) : ratio
  const tracks = `minmax(0, ${current}fr) 8px minmax(0, ${1 - current}fr)`

  return (
    <div ref={root} className={`split-pane split-${direction} ${className}`} style={horizontal ? { gridTemplateColumns: tracks } : { gridTemplateRows: tracks }}>
      <div className="split-content" id={`${id}-first`}>{first}</div>
      <div className="split-handle" role="separator" tabIndex={0} aria-label={label}
        aria-orientation={horizontal ? 'vertical' : 'horizontal'}
        aria-controls={`${id}-first ${id}-second`}
        aria-valuemin={Math.round(lower * 1000) / 10} aria-valuemax={Math.round(upper * 1000) / 10} aria-valuenow={Math.round(current * 1000) / 10}
        aria-valuetext={`${Math.round(current * 100)} percent`}
        title="Drag to resize · arrow keys to adjust · double-click to reset"
        onDoubleClick={() => setRatio(clamp(initial))}
        onKeyDown={(event) => {
          const decrease = horizontal ? 'ArrowLeft' : 'ArrowUp'
          const increase = horizontal ? 'ArrowRight' : 'ArrowDown'
          if (![decrease, increase, 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const step = (event.shiftKey ? 50 : 10) / available
          setRatio(event.key === 'Home' ? lower : event.key === 'End' ? upper : clamp(current + (event.key === increase ? step : -step)))
        }}
        onPointerDown={(event) => {
          if (event.button !== 0) return
          event.preventDefault()
          event.currentTarget.focus()
          event.currentTarget.setPointerCapture(event.pointerId)
          drag.current = { position: horizontal ? event.clientX : event.clientY, ratio: current }
        }}
        onPointerMove={(event) => {
          if (!drag.current) return
          const position = horizontal ? event.clientX : event.clientY
          setRatio(clamp(drag.current.ratio + (position - drag.current.position) / available))
        }}
        onPointerUp={(event) => {
          drag.current = null
          if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
        }}
        onPointerCancel={() => { drag.current = null }}
        onLostPointerCapture={() => { drag.current = null }}
      />
      <div className="split-content" id={`${id}-second`}>{second}</div>
    </div>
  )
}
