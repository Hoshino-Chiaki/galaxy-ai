import { memo, useEffect, useRef, useState, type ComponentProps } from 'react'
import { Check, Copy } from 'lucide-react'
import { Streamdown } from 'streamdown'
import { code } from '@streamdown/code'
import { cjk } from '@streamdown/cjk'
import 'streamdown/styles.css'
import './message-results.css'
import { copyText } from '../clipboard'

function MessageLink({ href, children }: ComponentProps<'a'>) {
  const safe = href && /^https?:\/\//i.test(href)
  return safe ? <a href={href} title={href} onClick={event => {
    event.preventDefault()
    void window.desktop?.openExternal(href).catch(() => {})
  }}>{children}</a> : <span>{children}</span>
}

const components = {
  a: MessageLink,
  img: ({ alt }: ComponentProps<'img'>) => <span className="message-image-placeholder">[图片{alt ? `：${alt}` : ''}]</span>,
  table: ({ children }: ComponentProps<'table'>) => <div className="markdown-table-scroll"><table>{children}</table></div>,
}

export default memo(function MarkdownMessage({ content, progressive = false, onRevealProgress }: { content: string; progressive?: boolean; onRevealProgress?: () => void }) {
  const [visibleLength, setVisibleLength] = useState(progressive ? 0 : content.length)
  useEffect(() => {
    if (!progressive) { setVisibleLength(content.length); return }
    setVisibleLength(0)
    const characters = new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(content)[Symbol.iterator]()
    const timer = setInterval(() => {
      const next = characters.next()
      if (next.done) { clearInterval(timer); return }
      setVisibleLength(next.value.index + next.value.segment.length)
    }, 20)
    return () => clearInterval(timer)
  }, [content, progressive])
  const visibleContent = progressive ? content.slice(0, visibleLength) : content
  const revealing = progressive && visibleLength < content.length
  useEffect(() => { if (progressive) onRevealProgress?.() }, [visibleLength, progressive, onRevealProgress])
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (copiedTimer.current) clearTimeout(copiedTimer.current) }, [])
  async function copy() {
    if (copiedTimer.current) clearTimeout(copiedTimer.current)
    setCopyError(false)
    try {
      await copyText(content)
      setCopied(true)
      copiedTimer.current = setTimeout(() => setCopied(false), 1800)
    } catch { setCopied(false); setCopyError(true) }
  }
  return <div className="assistant-response">
    <Streamdown className="message-text markdown-message" mode={revealing ? 'streaming' : 'static'} isAnimating={revealing} skipHtml controls={false}
      plugins={{ code, cjk }} components={components} shikiTheme={['github-dark', 'github-dark']}>
      {visibleContent}
    </Streamdown>
    {!revealing && !!content.trim() && <button type="button" className="copy-response" onClick={() => { void copy() }} aria-label={copied ? '已复制回复' : copyError ? '复制失败，点击重试' : '复制回复'}>
      {copied ? <Check size={13} /> : <Copy size={13} />}<span role="status">{copied ? '已复制' : copyError ? '复制失败，点击重试' : '复制'}</span>
    </button>}
  </div>
})
