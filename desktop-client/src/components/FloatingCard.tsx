import { useEffect, useRef, type PointerEvent, type ReactNode } from 'react'

/** Perspective and pointer parallax inspired by Aceternity's Comet Card interaction. */
export default function FloatingCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  const surface = useRef<HTMLDivElement>(null)
  const frame = useRef(0)
  useEffect(() => () => cancelAnimationFrame(frame.current), [])
  function reset() {
    cancelAnimationFrame(frame.current)
    surface.current?.removeAttribute('style')
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    if (event.pointerType !== 'mouse' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const rect = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - rect.left) / rect.width - .5
    const y = (event.clientY - rect.top) / rect.height - .5
    cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => {
      const style = surface.current?.style
      if (!style) return
      style.setProperty('--tilt-x', `${-y * 13}deg`)
      style.setProperty('--tilt-y', `${x * 13}deg`)
      style.setProperty('--shift-x', `${x * 7}px`)
      style.setProperty('--shift-y', `${y * 7}px`)
      style.setProperty('--light-x', `${(x + .5) * 100}%`)
      style.setProperty('--light-y', `${(y + .5) * 100}%`)
    })
  }
  return <div className={'floating-card-scene ' + className} onPointerMove={move} onPointerLeave={reset} onPointerCancel={reset}>
    <div ref={surface} className="floating-card-surface">{children}<span className="floating-card-light" aria-hidden="true" /></div>
  </div>
}
