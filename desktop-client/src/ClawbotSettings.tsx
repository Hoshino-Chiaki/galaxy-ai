import { useEffect, useState } from 'react'
import { Check, Link2, LoaderCircle, MessageCircle, QrCode, RefreshCw, Unplug } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { service } from './service'
import './clawbot.css'

type Connection = { connected: boolean; status: string; bot_id: string; connected_at: number | null; last_message_at: number | null; last_reply_at: number | null; error: string | null }
type Binding = { id: string; qr_content: string; expires_at: number; status: string }
const stamp = (value: number | null) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚无记录'
const labels: Record<string, string> = { wait: '用微信扫描二维码，并在手机上确认', scaned: '已扫码，请在手机上确认连接', scaned_but_redirect: '正在确认微信连接', need_verifycode: '请输入手机微信显示的配对码', expired: '二维码已失效，请重新获取', verify_code_blocked: '配对码输入次数过多，请重新获取二维码', failed: '本次绑定未完成', confirmed: '微信已连接' }

export default function ClawbotSettings() {
  const [connection, setConnection] = useState<Connection | null>(null)
  const [binding, setBinding] = useState<Binding | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [code, setCode] = useState('')
  useEffect(() => {
    let alive = true
    void service<Connection>('clawbot.settings').then(value => { if (alive) setConnection(value) }).catch(reason => { if (alive) setError((reason as Error).message) })
    return () => { alive = false }
  }, [])
  useEffect(() => {
    if (!binding || ['confirmed', 'failed', 'expired', 'verify_code_blocked'].includes(binding.status)) return
    let alive = true, inFlight = false
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const value = await service<{ status: string; error: string | null; connection: Connection }>('clawbot.poll', { id: binding.id })
        if (alive) { setBinding(old => old?.id === binding.id ? { ...old, status: value.status } : old); setConnection(value.connection); setError(value.error || '') }
      } catch (reason) { if (alive) setError((reason as Error).message) }
      finally { inFlight = false }
    }
    void poll(); const timer = setInterval(() => { void poll() }, 1500)
    return () => { alive = false; clearInterval(timer) }
  }, [binding?.id, binding?.status])
  useEffect(() => {
    if (!connection?.connected || binding && binding.status !== 'confirmed') return
    let alive = true, pending = false
    const timer = setInterval(async () => {
      if (pending) return
      pending = true
      try { const value = await service<Connection>('clawbot.settings'); if (alive) setConnection(value) }
      catch (reason) { if (alive) setError((reason as Error).message) }
      finally { pending = false }
    }, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [connection?.connected, binding?.status])
  async function connect() {
    setBusy(true); setError(''); setCode('')
    try { setBinding(await service<Binding>('clawbot.connect')) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(false) }
  }
  async function verify() {
    if (!binding || !code.trim()) return
    setBusy(true); setError('')
    try { await service('clawbot.poll', { id: binding.id, verify_code: code.trim() }); setCode('') }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(false) }
  }
  async function disconnect() {
    setBusy(true); setError('')
    try { setConnection(await service<Connection>('clawbot.disconnect')); setBinding(null) }
    catch (reason) { setError((reason as Error).message) }
    finally { setBusy(false) }
  }
  const connected = connection?.connected && connection.status === 'connected'
  return <div className="console-body clawbot-body">
    <div className="clawbot-heading"><span className="clawbot-logo"><MessageCircle size={23} strokeWidth={1.5} /></span><div><strong>ClawBot</strong><span>个人微信</span></div><span className={'clawbot-state' + (connected ? ' connected' : '')}>{connected ? <><Check size={12} />已连接</> : connection?.connected ? '需要检查连接' : '未连接'}</span></div>
    <p className="clawbot-description">扫码绑定你的微信，在 ClawBot 私聊中使用 Galaxy AI。沿用当前员工的权限与渠道账号，聊天记录独立保存。</p>
    <div className="clawbot-content">
      <div className="clawbot-connection">
        <div className="clawbot-section-title"><Link2 size={15} /><span>我的微信连接</span></div>
        {connection ? connection.connected ? <dl><div><dt>ClawBot 标识</dt><dd>{connection.bot_id}</dd></div><div><dt>绑定时间</dt><dd>{stamp(connection.connected_at)}</dd></div><div><dt>最近收到消息</dt><dd>{stamp(connection.last_message_at)}</dd></div><div><dt>最近回复</dt><dd>{stamp(connection.last_reply_at)}</dd></div></dl> : <p className="clawbot-muted">绑定后，向微信里的 ClawBot 发送一条文字消息即可开始。</p> : !error && <p className="clawbot-muted"><LoaderCircle className="spin" size={14} />正在读取连接状态</p>}
        <div className="clawbot-buttons"><button type="button" className="console-primary" disabled={busy} onClick={() => { void connect() }}>{busy ? <LoaderCircle size={14} className="spin" /> : binding || connection?.connected ? <RefreshCw size={14} /> : <QrCode size={14} />}{connection?.connected ? '重新扫码绑定' : binding ? '刷新二维码' : '扫码连接微信'}</button>{connection?.connected && <button type="button" className="console-ghost" disabled={busy} onClick={() => { void disconnect() }}><Unplug size={13} />解除本账号绑定</button>}</div>
      </div>
      {binding && binding.status !== 'confirmed' && <div className="clawbot-qr" aria-label="微信绑定二维码"><div className={'clawbot-qr-image' + (['expired', 'failed', 'verify_code_blocked'].includes(binding.status) ? ' expired' : '')}><QRCodeSVG value={binding.qr_content} size={192} marginSize={2} level="M" /></div><p role="status">{labels[binding.status] || '正在连接'}</p>{binding.status === 'need_verifycode' && <form onSubmit={event => { event.preventDefault(); void verify() }}><input aria-label="微信配对码" autoComplete="one-time-code" inputMode="numeric" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 12))} placeholder="手机显示的配对码" /><button className="console-primary" type="submit" disabled={busy || !code}>确认</button></form>}</div>}
    </div>
    <div className="clawbot-error" role="status" aria-live="polite">{error || connection?.error}</div>
    <p className="clawbot-note">当前接入文字私聊，暂不支持普通微信群。需要手机确认或配对码时，请按微信提示完成；解除此处绑定会停止 Galaxy AI 的收发。</p>
  </div>
}
