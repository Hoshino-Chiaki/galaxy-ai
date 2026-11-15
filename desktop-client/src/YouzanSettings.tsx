import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound, LoaderCircle, RefreshCw, Save, ShieldCheck, Store, X } from 'lucide-react'
import { service } from './service'

type Credential = { present: boolean; updated_at?: number | null; expires_at?: number | null; expired?: boolean }
type Renewal = { enabled: boolean; interval_days: number; next_at?: number | null; last_success_at?: number; paused?: boolean; error?: { code: string; message: string } | null }
type YouzanSettings = {
  configured: boolean; account: string; kdt_id: string; has_password: boolean; auto_refresh: boolean
  can_configure_renewal?: boolean; renewal?: { sid: Renewal; csrf: Renewal }
  sid: Credential; csrf: Credential; shops: { id: string; name: string }[]; shops_updated_at?: number | null
  multi_shop?: boolean; preparing_shops?: boolean; shop_sessions?: { kdt_id: string; name: string; ready: boolean; updated_at?: number | null; error?: { code: string; message: string } | null }[]
  job: { id?: string; kind?: 'sid' | 'csrf' | 'shops' | 'all'; status?: 'running' | 'success' | 'failed'; code?: string; message?: string; started_at?: number; finished_at?: number | null } | null
}
const date = (value?: number | null) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未获取'

export default function YouzanSettingsPanel() {
  const [config, setConfig] = useState<YouzanSettings | null>(null)
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [loadingShops, setLoadingShops] = useState(false)
  const [saving, setSaving] = useState(false)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [renewal, setRenewal] = useState({ sid: { enabled: true, interval_days: 2 }, csrf: { enabled: true, interval_days: 2 } })
  const [savingRenewal, setSavingRenewal] = useState(false)
  const alive = useRef(false)
  const automaticPreparation = useRef('')
  const running = config?.job?.status === 'running'
  const preparing = config?.preparing_shops === true
  const busy = saving || starting || running || preparing || loadingShops
  const dirty = !!config && (account.trim() !== config.account || password !== '')

  useEffect(() => {
    if (!config?.sid.present || config.sid.expired || busy || dirty || !config.shops.length) return
    const missing = !config.multi_shop || config.shop_sessions?.some(shop => !shop.ready && !shop.error)
    const generation = config.account + ':' + config.sid.updated_at
    if (!missing || automaticPreparation.current === generation) return
    automaticPreparation.current = generation
    void refresh('all')
  }, [config, busy, dirty])

  useEffect(() => {
    let valid = true
    alive.current = true
    void service<YouzanSettings>('youzan.settings').then(value => {
      if (!alive.current || !valid) return
      setConfig(value); setAccount(value.account)
      if (value.renewal) setRenewal({ sid: { enabled: value.renewal.sid.enabled, interval_days: value.renewal.sid.interval_days }, csrf: { enabled: value.renewal.csrf.enabled, interval_days: value.renewal.csrf.interval_days } })
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
    setSaving(true); setError(''); setNotice('')
    try {
      const value = await service<YouzanSettings>('youzan.save', { account: account.trim(), password })
      if (alive.current) {
        setConfig(old => ({ ...old, ...value })); setAccount(value.account); setPassword('')
        setNotice('配置已保存。登录后由服务器自动准备各店铺会话。')
      }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }

  async function refresh(kind: 'sid' | 'csrf' | 'shops' | 'all') {
    if (busy || dirty) return
    setStarting(true); setError(''); setNotice('')
    try {
      const value = await service<YouzanSettings>('youzan.refresh', { kind })
      if (alive.current) setConfig(old => ({ ...old, ...value }))
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

  async function saveRenewal(event: FormEvent) {
    event.preventDefault()
    if (savingRenewal) return
    setSavingRenewal(true); setError(''); setNotice('')
    try {
      const value = await service<YouzanSettings>('youzan.renewal', renewal)
      if (alive.current) { setConfig(value); setNotice('自动刷新设置已保存，关闭客户端后服务器仍会按计划执行。') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSavingRenewal(false) }
  }

  return <div className="console-body youzan-settings">
    <div className="console-page-heading"><div><h1>有赞</h1><p>配置登录账号，自动准备各店铺的访问会话。</p></div><span className="console-status"><span className="status-dot" />{config?.auto_refresh ? '自动刷新已启用' : '手动刷新'}</span></div>
    <div className="console-notification" role="status" aria-live="polite">{error ? <span className="console-error">{error}</span> : notice ? <span className="console-success"><CheckCircle2 size={14} />{notice}</span> : null}</div>
    {!config ? <div className="console-empty"><LoaderCircle size={22} className="spin" /><p>{error ? '无法读取有赞配置' : '正在读取配置'}</p></div> : <>
      <form className="youzan-account-card" onSubmit={save}>
        <div className="youzan-section-title"><span className="provider-symbol"><KeyRound size={18} /></span><div><h2>登录账号</h2><p>凭证保存在服务器，按当前用户独立管理。</p></div></div>
        <div className="console-form-grid">
          <div className="console-field"><label htmlFor="youzan-account">有赞账号</label><input id="youzan-account" autoComplete="off" spellCheck={false} maxLength={160} value={account} disabled={busy} required placeholder="登录手机号 / 账号" onChange={event => { setAccount(event.target.value); setNotice('') }} /></div>
          <div className="console-field"><label htmlFor="youzan-password">密码{config.has_password && <span>留空保持原密码</span>}</label><input id="youzan-password" type="password" autoComplete="new-password" maxLength={256} value={password} disabled={busy} required={!config.has_password || account.trim() !== config.account} placeholder={config.has_password ? '已加密保存' : '输入有赞登录密码'} onChange={event => { setPassword(event.target.value); setNotice('') }} /></div>
        </div>

        <div className="youzan-account-footer"><span><ShieldCheck size={13} />密码加密保存，不回显</span><button type="submit" className="console-primary" disabled={busy || (!dirty && config.configured)}>{saving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存配置</button></div>
      </form>
      {config.can_configure_renewal && <form className="youzan-renewal" onSubmit={saveRenewal} aria-label="自动刷新设置">
        <div className="console-section-heading"><h2>自动刷新</h2><span>仅管理员可配置 · 默认间隔 2 天</span></div>
        <div className="youzan-renewal-grid">{(['sid', 'csrf'] as const).map(kind => {
          const status = config.renewal?.[kind]
          return <div className="youzan-renewal-item" key={kind}>
            <label className="youzan-renewal-toggle"><input type="checkbox" checked={renewal[kind].enabled} disabled={savingRenewal} onChange={e => setRenewal(old => ({ ...old, [kind]: { ...old[kind], enabled: e.target.checked } }))} />{kind === 'sid' ? 'SID 自动检查与刷新' : '全部店铺 CRM CSRF 自动刷新'}</label>
            <label className="youzan-renewal-interval">每<input aria-label={kind === 'sid' ? 'SID 刷新间隔天数' : 'CRM CSRF 刷新间隔天数'} type="number" min="0.001" step="any" required value={renewal[kind].interval_days} disabled={savingRenewal} onChange={e => setRenewal(old => ({ ...old, [kind]: { ...old[kind], interval_days: Number(e.target.value) } }))} />天执行一次</label>
            <small>下次执行：{status?.paused ? '已暂停，需人工处理' : status?.enabled && status.next_at ? date(status.next_at) : '未启用'}</small>
            {status?.last_success_at && <small>最近成功：{date(status.last_success_at)}</small>}
            {status?.error && <p className="console-error">{status.error.message}</p>}
          </div>
        })}</div>
        <div className="youzan-renewal-footer"><p>刷新间隔不代表凭证有效期。需要短信或安全验证时会暂停并显示原因。</p><button className="console-primary" type="submit" disabled={savingRenewal || !config.configured}>{savingRenewal ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存自动刷新</button></div>
      </form>}
      <section className="console-section youzan-credentials" aria-label="有赞访问凭证">
        <div className="console-section-heading"><h2>访问凭证</h2><span>{dirty ? '请先保存配置，再刷新' : '保存配置不会自动登录'}</span></div>
        <div className="youzan-credential-grid">
          <article className="youzan-credential-card"><div className="youzan-credential-heading"><h3>SID</h3><span className={`youzan-badge${config.sid.present && !config.sid.expired ? ' ready' : ''}`}>{config.sid.expired ? '已到浏览器期限' : config.sid.present ? '已获取' : '未获取'}</span></div><p>商家后台的登录会话</p>
            <dl><div><dt>最近获取</dt><dd>{date(config.sid.updated_at)}</dd></div><div><dt>浏览器到期时间</dt><dd>{config.sid.present ? config.sid.expires_at ? date(config.sid.expires_at) : '未提供固定时间' : '—'}</dd></div></dl>
            <button type="button" className="console-ghost" disabled={busy || dirty || !config.configured} onClick={() => { void refresh('sid') }}>{running && config.job?.kind === 'sid' ? <LoaderCircle size={13} className="spin" /> : <RefreshCw size={13} />}{config.kdt_id ? '刷新 SID 和 CRM' : '获取 SID'}</button>
          </article>
          <article className="youzan-credential-card"><div className="youzan-credential-heading"><h3>CRM CSRF</h3><span className={`youzan-badge${config.csrf.present && !config.csrf.expired ? ' ready' : ''}`}>{config.csrf.expired && config.csrf.present ? '需刷新 SID' : config.csrf.present ? '已获取' : '未获取'}</span></div><p>按店铺分别保存，查询时自动使用对应会话</p>
            <dl><div><dt>最近获取</dt><dd>{date(config.csrf.updated_at)}</dd></div><div><dt>获取方式</dt><dd>复用登录会话，后台读取</dd></div></dl>
            <button type="button" className="console-ghost" disabled={busy || dirty || !config.sid.present || config.sid.expired} onClick={() => { void refresh('all') }}>{running && config.job?.kind === 'all' ? <LoaderCircle size={13} className="spin" /> : <RefreshCw size={13} />}刷新全部店铺会话</button>
          </article>
        </div>
        {!!config.shop_sessions?.length && <div className="shop-session-list" aria-label="店铺会话状态"><div className="console-section-heading"><h2>店铺会话</h2><span>{config.shop_sessions.filter(shop => shop.ready).length} / {config.shop_sessions.length} 家已获取</span></div>
          {config.shop_sessions.map(shop => <div className="shop-session-row" key={shop.kdt_id}><Store size={14} /><div><strong>{shop.name}</strong><small>{shop.error?.message || date(shop.updated_at)}</small></div><span className={'youzan-badge' + (shop.ready ? ' ready' : '')}>{shop.ready ? '已获取' : shop.error ? '获取失败' : '待获取'}</span></div>)}
        </div>}
      </section>
      {config.job?.status && <div className={`youzan-job ${config.job.status}`} role="status" aria-live="polite"><span>{running ? <LoaderCircle size={15} className="spin" /> : config.job.status === 'success' ? <CheckCircle2 size={15} /> : <X size={15} />}</span><div><p>{config.job.message}</p><small>{running ? '正在由服务器处理，离开页面后任务仍会继续。' : `完成于 ${date(config.job.finished_at)}`}</small></div>{running && <button type="button" className="console-ghost" disabled={starting} onClick={() => { void cancel() }}>取消</button>}</div>}
      {preparing && !running && <div className="youzan-job" role="status"><LoaderCircle size={15} className="spin" /><p>正在自动准备店铺会话…</p></div>}
      <p className="youzan-footnote">优先复用现有登录会话，按店铺准备 CRM 信息。自动刷新由服务器执行，失败不会覆盖已保存的凭证。</p>
    </>}
  </div>
}
