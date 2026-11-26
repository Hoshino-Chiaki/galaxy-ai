import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowRight, Clock3, LoaderCircle, MessageSquareText, ShieldCheck, X } from 'lucide-react'
import './verification.css'

type Props = { request: VerificationRequest; onSubmit: (code: string) => Promise<void>; onCancel: () => Promise<void> }
export default function VerificationDialog({ request, onSubmit, onCancel }: Props) {
  const panel = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const anchor = useRef({ at: performance.now(), remaining: request.expires_at_ms - request.server_now_ms })
  const [remaining, setRemaining] = useState(Math.max(0, Math.ceil(anchor.current.remaining / 1000)))
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [accepted, setAccepted] = useState(false)
  const [error, setError] = useState('')
  const cancelRef = useRef(onCancel)
  cancelRef.current = onCancel
  useEffect(() => {
    anchor.current = { at: performance.now(), remaining: request.expires_at_ms - request.server_now_ms }
  }, [request.expires_at_ms, request.server_now_ms])
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    input.current?.focus()
    const timer = window.setInterval(() => setRemaining(Math.max(0, Math.ceil((anchor.current.remaining - performance.now() + anchor.current.at) / 1000))), 250)
    function keyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') { event.preventDefault(); void cancelRef.current().catch(reason => setError((reason as Error).message)) }
      if (event.key !== 'Tab') return
      const controls = panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled)')
      if (!controls?.length) return
      const first = controls[0], last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keyboard)
    return () => { window.clearInterval(timer); document.removeEventListener('keydown', keyboard); previous?.focus() }
  }, [])
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || accepted || !remaining || !/^[A-Za-z0-9]{4,16}$/.test(code)) return
    setBusy(true); setError('')
    try { await onSubmit(code); setCode(''); setAccepted(true) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(false) }
  }
  async function cancel() {
    if (busy) return
    setBusy(true); setError('')
    try { await onCancel(); setCode(''); setAccepted(true) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(false) }
  }
  return <div className="verification-backdrop">
    <div className="verification-dialog" ref={panel} role="dialog" aria-modal="true" aria-labelledby="verification-title" aria-describedby="verification-description">
      <button className="verification-close" type="button" aria-label="取消验证码验证" disabled={busy} onClick={() => { void cancel() }}><X size={18} /></button>
      <div className="verification-emblem"><MessageSquareText size={26} strokeWidth={1.5} /><span><ShieldCheck size={13} /></span></div>
      <span className="verification-eyebrow">安全验证</span>
      <h2 id="verification-title">请输入短信验证码</h2>
      <p id="verification-description">验证码已发送至 <strong>{request.destination}</strong></p>
      <div className="verification-purpose">{request.purpose}</div>
      <form onSubmit={submit}>
        <label htmlFor="channel-verification-code">验证码</label>
        <input ref={input} id="channel-verification-code" className="verification-code" type="text" inputMode="numeric" autoComplete="one-time-code" autoCapitalize="off" spellCheck={false}
          placeholder="输入验证码" value={code} onChange={event => { setCode(event.target.value.replace(/\s/g, '')); setError('') }} minLength={4} maxLength={16}
          disabled={busy || accepted || !remaining} aria-invalid={!!error} aria-describedby={error ? 'verification-error' : 'verification-countdown'} />
        <div className={'verification-countdown' + (remaining <= 30 ? ' is-urgent' : '')} id="verification-countdown"><Clock3 size={14} /><span>{remaining ? '请在倒计时结束前提交' : '等待已超时'}</span><strong>{Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}</strong></div>
        <div className="verification-time-track" aria-hidden="true"><i style={{ transform: `scaleX(${Math.min(1, remaining / 120)})` }} /></div>
        {error && <p className="verification-error" id="verification-error" role="alert">{error}</p>}
        {accepted && <p className="verification-received" role="status">已收到，正在继续处理…</p>}
        <div className="verification-actions"><button type="button" disabled={busy || accepted} onClick={() => { void cancel() }}>取消验证</button><button type="submit" disabled={busy || accepted || !remaining || !/^[A-Za-z0-9]{4,16}$/.test(code)}>{busy ? <LoaderCircle size={16} className="spin" /> : <>验证并继续<ArrowRight size={15} /></>}</button></div>
      </form>
      <p className="verification-footnote"><ShieldCheck size={12} />验证码仅用于本次验证，不会显示在对话中</p>
    </div>
  </div>
}
