import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, Database, KeyRound, LoaderCircle, LogIn, RefreshCw, ShieldCheck } from 'lucide-react'
import { service } from './service'
type Settings = { account: string; has_password: boolean; connected: boolean; cookie: { present: boolean }; sid: { present: boolean }; database: string; database_name?: string; last_login_at: number | null }
type Databases = { databases: { database: string; name: string }[]; default_database: string | null }
export default function GuanjiaSettings() {
  const [config, setConfig] = useState<Settings | null>(null)
  const [account, setAccount] = useState(''), [password, setPassword] = useState('')
  const [passwordChanged, setPasswordChanged] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [databases, setDatabases] = useState<Databases['databases']>([])
  const [database, setDatabase] = useState('')
  const [databaseError, setDatabaseError] = useState('')
  const [loadingDatabases, setLoadingDatabases] = useState(true)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    let valid = true
    void Promise.allSettled([service<Settings>('guanjia.settings'), service<Databases>('guanjia.databases')]).then(([settings, choices]) => {
      if (!valid) return
      if (settings.status === 'fulfilled') { setConfig(settings.value); setAccount(settings.value.account); setPassword(settings.value.has_password ? '••••••••' : '') }
      else setError((settings.reason as Error).message)
      if (choices.status === 'fulfilled') {
        setDatabases(choices.value.databases)
        const previous = settings.status === 'fulfilled' ? settings.value.database : ''
        setDatabase(choices.value.databases.some(item => item.database === previous) ? previous : previous ? '' : choices.value.default_database || '')
        if (previous && !choices.value.databases.some(item => item.database === previous)) setDatabaseError('原账套不在当前列表中，请重新选择。')
      } else setDatabaseError((choices.reason as Error).message)
      setLoadingDatabases(false)
    })
    return () => { valid = false; alive.current = false }
  }, [])
  async function refreshDatabases() {
    setLoadingDatabases(true); setDatabaseError('')
    try {
      const value = await service<Databases>('guanjia.databases')
      if (alive.current) {
        setDatabases(value.databases)
        setDatabase(previous => value.databases.some(item => item.database === previous) ? previous : '')
      }
    } catch (reason) { if (alive.current) setDatabaseError((reason as Error).message) }
    finally { if (alive.current) setLoadingDatabases(false) }
  }
  async function login(event: FormEvent) {
    event.preventDefault(); if (busy || loadingDatabases || !database) return
    setBusy(true); setError(''); setNotice('')
    try {
      const value = await service<Settings>('guanjia.login', { account: account.trim(), database,
        ...(passwordChanged || !config?.has_password || account.trim() !== config.account ? { password: password || null } : {}) })
      if (alive.current) { setConfig(value); setAccount(value.account); setDatabase(value.database); setPassword(value.has_password ? '••••••••' : ''); setPasswordChanged(false); setNotice(`已登录${value.database_name ? '「' + value.database_name + '」' : '所选账套'}，Cookie 和 SID 已自动获取并保存。`) }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  return <div className="console-body guanjia-settings">
    <form className="youzan-account-card" onSubmit={login} aria-label="管家婆登录">
      <div className="youzan-section-title"><span className="provider-symbol"><KeyRound size={18} aria-hidden="true" /></span><div><h2>管家婆账号</h2><p>登录后自动获取访问凭证。</p></div></div>
      <div className="console-field guanjia-database-field"><label htmlFor="guanjia-database"><Database size={13} />账套</label><div className="guanjia-database-control"><select id="guanjia-database" required value={database} disabled={busy || loadingDatabases || !databases.length} onChange={event => { setDatabase(event.target.value); setDatabaseError(''); setNotice('') }}><option value="">{loadingDatabases ? '正在读取账套…' : '请选择账套'}</option>{databases.map(item => <option key={item.database} value={item.database}>{item.name}</option>)}</select><button type="button" className="console-ghost" disabled={busy || loadingDatabases} aria-label="刷新账套列表" title="刷新账套列表" onClick={() => { void refreshDatabases() }}><RefreshCw size={14} className={loadingDatabases ? 'spin' : ''} /></button></div>{databaseError && <span className="console-error" role="status">{databaseError}</span>}<span className="console-field-hint">登录所选账套；已保存的连接在登录成功后才会更新。</span></div>
      <div className="console-form-grid">
        <div className="console-field"><label htmlFor="guanjia-account">账号</label><input id="guanjia-account" autoComplete="username" required maxLength={160} value={account} disabled={busy || !config} onChange={event => { setAccount(event.target.value); setPassword(''); setPasswordChanged(true); setNotice('') }} /></div>
        <div className="console-field"><label htmlFor="guanjia-password">密码<span>{config?.has_password && !passwordChanged ? '已保存，清空表示无需密码' : '可留空，表示无需密码'}</span></label><input id="guanjia-password" type="password" autoComplete="new-password" maxLength={256} value={password} disabled={busy || !config} placeholder="无需密码时留空" onFocus={event => { if (!passwordChanged) event.currentTarget.select() }} onChange={event => { setPassword(event.target.value); setPasswordChanged(true); setNotice('') }} /></div>
      </div>
      <div className="youzan-account-footer"><span><ShieldCheck size={13} aria-hidden="true" />凭证加密保存在服务器</span><button type="submit" className="console-primary" disabled={busy || !config || !account.trim() || loadingDatabases || !database}>{busy ? <LoaderCircle size={14} className="spin" /> : <LogIn size={14} />}{busy ? '正在登录…' : '登录'}</button></div>
    </form>
    {!config && !error && <div className="console-empty" role="status"><LoaderCircle size={20} className="spin" />正在读取配置</div>}
    <div className="console-notification" role="status" aria-live="polite">{error ? <span className="console-error">{error}</span> : notice ? <span className="console-success"><CheckCircle2 size={14} />{notice}</span> : null}</div>
    {config && <section className="console-section" aria-label="管家婆访问凭证"><div className="console-section-heading"><h2>访问凭证</h2><span>{config.last_login_at ? `最近登录：${new Date(config.last_login_at * 1000).toLocaleString('zh-CN', { hour12: false })}` : '尚未登录'}</span></div>
      <div className="youzan-credential-grid">{(['cookie', 'sid'] as const).map(kind => <article key={kind} className="youzan-credential-card"><div className="youzan-credential-heading"><h3>{kind === 'cookie' ? 'Cookie' : 'SID'}</h3><span className={'youzan-badge' + (config[kind].present ? ' ready' : '')}>{config[kind].present ? '已获取' : '未获取'}</span></div><p>{kind === 'cookie' ? '管家婆接口访问凭证' : 'ASP.NET 登录会话标识'}</p></article>)}</div>
      {config.database && <p className="console-section-description">当前已登录账套：{config.database_name || databases.find(item => item.database === config.database)?.name || config.database} <span className="guanjia-database-id">{config.database}</span></p>}
    </section>}
  </div>
}
