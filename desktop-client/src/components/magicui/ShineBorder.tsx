// Adapted from Magic UI's Shine Border (MIT, Copyright (c) Magic UI).
// Source: https://magicui.design/r/shine-border.json
// License is distributed in public/licenses/magicui.txt.
import type { CSSProperties, HTMLAttributes } from 'react'
import './shine-border.css'

type Props = HTMLAttributes<HTMLDivElement> & {
  borderWidth?: number
  duration?: number
  shineColor?: string | string[]
}

export function ShineBorder({ borderWidth = 1, duration = 14, shineColor = '#000000', className = '', style, ...props }: Props) {
  return <div aria-hidden="true" className={`magic-shine-border ${className}`} style={{
    '--border-width': `${borderWidth}px`,
    '--duration': `${duration}s`,
    backgroundImage: `radial-gradient(transparent, transparent, ${Array.isArray(shineColor) ? shineColor.join(',') : shineColor}, transparent, transparent)`,
    backgroundSize: '300% 300%',
    mask: 'linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)',
    WebkitMask: 'linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)',
    WebkitMaskComposite: 'xor',
    maskComposite: 'exclude',
    padding: 'var(--border-width)',
    ...style,
  } as CSSProperties} {...props} />
}
