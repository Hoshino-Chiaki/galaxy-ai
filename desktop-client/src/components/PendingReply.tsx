import { useEffect, useRef } from 'react'
import SiriWave from 'siriwave'

export function formatDuration(milliseconds: number) {
  const seconds = Math.max(0, milliseconds) / 1000
  if (seconds < 1) return '不到 1 秒'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`
}

type Props = { stopping: boolean; active: boolean }

export default function PendingReply({ stopping, active }: Props) {
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!container.current) return
    const wave = new SiriWave({
      container: container.current,
      style: 'ios9',
      width: 200, height: 40, cover: true,
      ratio: Math.min(window.devicePixelRatio || 1, 2),
      amplitude: .65, speed: .1, autostart: false,
    })
    // The pending waveform is an explicit activity indicator on every PC.
    // Pause only while stopped, hidden or outside the visible conversation.
    let inView = true
    function synchronize() {
      if (active && !stopping && !document.hidden && inView) wave.start()
      else wave.stop()
    }
    const observer = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting
      synchronize()
    })
    observer.observe(container.current)
    document.addEventListener('visibilitychange', synchronize)
    synchronize()
    return () => {
      observer.disconnect()
      document.removeEventListener('visibilitychange', synchronize)
      wave.dispose()
    }
  }, [active, stopping])

  return <div className="reply-wave" data-stopping={stopping} role="status" aria-live="polite">
    <span className="sr-only">{stopping ? '正在停止回复' : 'AI 正在回复'}</span>
    <div ref={container} className="reply-wave-canvas" aria-hidden="true" />
    <span className="reply-wave-static" aria-hidden="true" />
  </div>
}
