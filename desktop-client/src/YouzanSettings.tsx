import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound, LoaderCircle, ShieldCheck, Store, X } from 'lucide-react'
import { service } from './service'

type Credential = { present: boolean; updated_at?: number | null; expires_at?: number | null; expired?: boolean }
type PointerSample = { x: number; y: number; ms: number }
type Verification = { id: string; kind: 'send' | 'code' | 'slider'; rejected?: boolean; image?: string }
type YouzanSettings = {
  verification?: Verification | null
  configured: boolean; account: string; kdt_id: string; has_password: boolean; auto_refresh: boolean
  sid: Credential; csrf: Credential; shops: { id: string; name: string }[]; shops_updated_at?: number | null
  multi_shop?: boolean; preparing_shops?: boolean; shop_sessions?: { kdt_id: string; name: string; ready: boolean; updated_at?: number | null; error?: { code: string; message: string } | null }[]
  job: { id?: string; kind?: 'sid' | 'csrf' | 'shops' | 'all'; status?: 'running' | 'success' | 'failed'; code?: string; message?: string; started_at?: number; finished_at?: number | null } | null
}
const date = (value?: number | null) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未获取'

export default function YouzanSettingsPanel() {
  const [config, setConfig] = useState<YouzanSettings | null>(null)
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [passwordChanged, setPasswordChanged] = useState(false)
  const [loadingShops, setLoadingShops] = useState(false)
  const [saving, setSaving] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [smsCode, setSmsCode] = useState('')
  const drag = useRef<{ x: number; y: number; ms: number; points: PointerSample[] } | null>(null)
  const alive = useRef(false)
  const running = config?.job?.status === 'running'
  const preparing = config?.preparing_shops === true
  const busy = saving || starting || running || preparing || loadingShops
  const verification = config?.verification

  useEffect(() => {
    let valid = true
    alive.current = true
    void service<YouzanSettings>('youzan.settings').then(value => {
      if (!alive.current || !valid) return
      setConfig(value); setAccount(value.account); setPassword(value.has_password ? '••••••••' : '')
      if (value.sid.present && !value.sid.expired && value.job?.status !== 'running' && !value.preparing_shops) {
        setLoadingShops(true)
        void service<YouzanSettings>('youzan.shops').then(next => {
          if (alive.current && valid) setConfig(next)
        }).catch(reason => { if (alive.current && valid) setError((reason as Error).message) })
          .finally(() => { if (alive.current && valid) setLoadingShops(false) })
      }
    }).catch(reason => { if (alive.current && valid) setError((reason as Error).message) })
    return () => { valid = false; alive.current = false }
  }, [])

  useEffect(() => {
    let stopped = false, inFlight = false
    const timer = window.setInterval(() => {
      if (inFlight) return
      inFlight = true
      void service<YouzanSettings>('youzan.settings').then(value => {
        if (!stopped && alive.current) { setConfig(value); setError('') }
      }).catch(() => {
        if (!stopped && alive.current) setError('暂时无法读取刷新进度，正在重试。')
      }).finally(() => { inFlight = false })
    }, running || preparing ? 2000 : 30000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [running, preparing])

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setSaving(true); setError('')
    try {
      const value = await service<YouzanSettings>('youzan.save', { account: account.trim(), interactive_verification: true,
        ...(passwordChanged || !config?.has_password || account.trim() !== config.account ? { password: password || null } : {}) })
      if (alive.current) {
        setConfig(old => ({ ...old, ...value })); setAccount(value.account); setPassword(value.has_password ? '••••••••' : ''); setPasswordChanged(false)
      }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }

  async function submitVerification(fields = {}) {
    if (!verification || starting) return
    setStarting(true); setError('')
    try {
      const next = await service<YouzanSettings>('youzan.verification', {
        id: verification.id, kind: verification.kind, ...fields,
      })
      if (alive.current) { setConfig(next); setSmsCode('') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setStarting(false) }
  }

  async function cancel() {
    setStarting(true); setError('')
    try {
      const value = await service<YouzanSettings>('youzan.cancel')
      if (alive.current) setConfig(old => ({ ...old, ...value }))
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setStarting(false) }
  }

  return <div className="console-body youzan-settings">
    <div className="console-status-toolbar"><span className="console-status"><span className="status-dot" />个人账号</span></div>
    <div className="console-notification" role="status" aria-live="polite">{error ? <span className="console-error">{error}</span> : null}</div>
    {!config ? <div className="console-empty"><LoaderCircle size={22} className="spin" /><p>{error ? '无法读取有赞配置' : '正在读取配置'}</p></div> : <>
      <form className="youzan-account-card" onSubmit={save}>
        <div className="youzan-section-title"><span className="provider-symbol"><KeyRound size={18} /></span><div><h2>登录账号</h2><p>凭证保存在服务器，按当前用户独立管理。</p></div></div>
        <div className="console-form-grid">
          <div className="console-field"><label htmlFor="youzan-account">有赞账号</label><input id="youzan-account" autoComplete="off" spellCheck={false} maxLength={160} value={account} disabled={busy} required placeholder="登录手机号 / 账号" onChange={event => { setAccount(event.target.value); setPassword(''); setPasswordChanged(true) }} /></div>
          <div className="console-field"><label htmlFor="youzan-password">密码<span>{config.has_password && !passwordChanged ? '已保存，清空表示无需密码' : '可留空，表示无需密码'}</span></label><input id="youzan-password" type="password" autoComplete="new-password" maxLength={256} value={password} disabled={busy} placeholder="无需密码时留空" onFocus={event => { if (!passwordChanged) event.currentTarget.select() }} onChange={event => { setPassword(event.target.value); setPasswordChanged(true) }} /></div>
        </div>

        <div className="youzan-account-footer"><span><ShieldCheck size={13} />密码加密保存，不回显</span><button type="submit" className="console-primary" disabled={busy}>{saving ? <LoaderCircle size={14} className="spin" /> : <KeyRound size={14} />}连接有赞</button></div>
      </form>
      {verification && <div className="youzan-account-card" key={verification.id}>
        {verification.kind === 'send' && <button type="button" className="console-primary" disabled={starting}
          onClick={() => { void submitVerification() }}>发送验证码</button>}
        {verification.kind === 'code' && <form onSubmit={event => {
          event.preventDefault()
          if (/^[0-9]{6}$/.test(smsCode)) void submitVerification({ code: smsCode })
        }}>
          <div className="console-field">
            <label htmlFor="youzan-sms-code">{verification.rejected ? '验证码未通过，请重新输入' : '请输入短信中的 6 位验证码'}</label>
            <input id="youzan-sms-code" autoComplete="one-time-code" inputMode="numeric" maxLength={6}
              pattern="[0-9]{6}" required value={smsCode} disabled={starting}
              onChange={event => setSmsCode(event.target.value.replace(/[^0-9]/g, ''))} />
          </div>
          <button type="submit" className="console-primary" disabled={starting || smsCode.length !== 6}>提交验证码</button>
        </form>}
        {verification.kind === 'slider' && verification.image && <>
          <p>请拖动图中的滑块完成有赞验证。</p>
          <img alt="有赞滑块验证码" src={`data:image/jpeg;base64,${verification.image}`}
            draggable={false} style={{ maxWidth: '100%', touchAction: 'none', cursor: 'grab' }}
            onPointerDown={event => {
              if (starting || event.button !== 0) return
              event.currentTarget.setPointerCapture(event.pointerId)
              drag.current = { x: event.clientX, y: event.clientY, ms: performance.now(), points: [] }
            }}
            onPointerMove={event => {
              const active = drag.current
              if (!active || active.points.length >= 159) return
              const scale = event.currentTarget.naturalWidth / event.currentTarget.clientWidth
              const ms = Math.round(performance.now() - active.ms)
              if (ms <= 10000) active.points.push({
                x: Math.max(-500, Math.min(500, Math.round((event.clientX - active.x) * scale))),
                y: Math.max(-400, Math.min(400, Math.round((event.clientY - active.y) * scale))), ms,
              })
            }}
            onPointerCancel={() => { drag.current = null }}
            onPointerUp={() => {
              const active = drag.current
              drag.current = null
              if (active && active.points.length >= 2) void submitVerification({ points: active.points })
            }} />
        </>}
      </div>}
      <section className="console-section youzan-credentials" aria-label="授权店铺">
        {!!config.shop_sessions?.length && <div className="shop-session-list" aria-label="店铺会话状态"><div className="console-section-heading"><h2>授权店铺</h2><span>{config.shop_sessions.filter(shop => shop.ready).length} / {config.shop_sessions.length} 家已获取</span></div>
          {config.shop_sessions.map(shop => <div className="shop-session-row" key={shop.kdt_id}><Store size={14} /><div><strong>{shop.name}</strong><small>{shop.error?.message || date(shop.updated_at)}</small></div><span className={'youzan-badge' + (shop.ready ? ' ready' : '')}>{shop.ready ? '已获取' : shop.error ? '获取失败' : '待获取'}</span></div>)}
        </div>}
      </section>
      {config.job?.status && <div className={`youzan-job ${config.job.status}`} role="status" aria-live="polite"><span>{running ? <LoaderCircle size={15} className="spin" /> : config.job.status === 'success' ? <CheckCircle2 size={15} /> : <X size={15} />}</span><div><p>{config.job.message}</p><small>{running ? (verification ? '请在当前账号完成验证。' : '正在由服务器处理。') : `完成于 ${date(config.job.finished_at)}`}</small></div>{running && <button type="button" className="console-ghost" disabled={starting} onClick={() => { void cancel() }}>取消</button>}</div>}
      {preparing && !running && <div className="youzan-job" role="status"><LoaderCircle size={15} className="spin" /><p>正在自动准备店铺会话…</p></div>}
      <p className="youzan-footnote">优先复用现有登录会话，按店铺准备 CRM 信息。刷新失败不会覆盖已保存的凭证。</p>
    </>}
  </div>
}
