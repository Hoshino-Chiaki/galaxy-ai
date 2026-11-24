import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound, LoaderCircle, LogIn, ShieldCheck } from 'lucide-react'
import { service } from './service'

type Settings = { account: string; has_password: boolean; connected: boolean; identity: { uid: string | number; coId: string | number; user_name?: string } | null; last_login_at: number | null; expires_at: number | null }

export default function JushuitanSettings() {
  const [config, setConfig] = useState<Settings | null>(null)
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [passwordChanged, setPasswordChanged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const alive = useRef(true)
  useEffect(() => {
    let current = true; alive.current = true
    void service<Settings>('jushuitan.settings').then(value => {
      if (!current) return
      setConfig(value); setAccount(value.account); setPassword(value.has_password ? '••••••••' : '')
    }).catch(reason => { if (current) setError((reason as Error).message) })
    return () => { current = false; alive.current = false }
  }, [])
  async function login(event: FormEvent) {
    event.preventDefault(); if (busy || !config) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await service<Settings>('jushuitan.login', { account: account.trim(),
        ...(passwordChanged || !config.has_password || account.trim() !== config.account ? { password: password || null } : {}) })
      if (!alive.current) return
      setConfig(result); setPassword(result.has_password ? '••••••••' : ''); setPasswordChanged(false)
      setNotice('已登录聚水潭供应链，访问凭证已加密保存。')
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  return <div className="console-body">
    <form className="youzan-account-card" onSubmit={login} aria-label="聚水潭登录">
      <div className="youzan-section-title"><span className="provider-symbol"><KeyRound size={18} /></span><div><h2>聚水潭账号</h2><p>查询供应链订单，使用当前账号的渠道权限。</p></div></div>
      <div className="console-form-grid">
        <div className="console-field"><label htmlFor="jushuitan-account">账号</label><input id="jushuitan-account" autoComplete="username" maxLength={160} required disabled={busy || !config} value={account} onChange={event => { setAccount(event.target.value); setPassword(''); setPasswordChanged(true); setNotice('') }} /></div>
        <div className="console-field"><label htmlFor="jushuitan-password">密码<span>{config?.has_password && !passwordChanged ? '已保存，清空表示无需密码' : '可留空，表示无需密码'}</span></label><input id="jushuitan-password" type="password" autoComplete="new-password" maxLength={256} disabled={busy || !config} value={password} onFocus={event => { if (!passwordChanged) event.currentTarget.select() }} onChange={event => { setPassword(event.target.value); setPasswordChanged(true); setNotice('') }} /></div>
      </div>
      <div className="youzan-account-footer"><span><ShieldCheck size={13} />凭证按员工独立加密保存</span><button className="console-primary" type="submit" disabled={busy || !config || !account.trim()}>{busy ? <LoaderCircle size={14} className="spin" /> : <LogIn size={14} />}{busy ? '正在登录…' : config?.connected ? '重新登录' : '保存并登录'}</button></div>
    </form>
    {!config && !error && <div className="console-empty" role="status"><LoaderCircle size={20} className="spin" />正在读取配置</div>}
    <div className="console-notification" role="status" aria-live="polite">{error ? <span className="console-error">{error}</span> : notice ? <span className="console-success"><CheckCircle2 size={14} />{notice}</span> : null}</div>
    {config && <section className="console-section" aria-label="聚水潭连接状态"><div className="console-section-heading"><h2>连接状态</h2><span className={'youzan-badge' + (config.connected ? ' ready' : '')}>{config.connected ? '访问凭证已保存' : '尚未登录'}</span></div>
      <p className="console-section-description">{config.identity ? `渠道账号：${config.identity.user_name || config.account} · 公司：${config.identity.coId}` : '登录成功后，AI 将使用你自己的供应链账号查询订单。'}</p>
      {config.last_login_at && <p className="console-section-description">最近登录：{new Date(config.last_login_at * 1000).toLocaleString('zh-CN', { hour12: false })}</p>}
    </section>}
  </div>
}
