import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, KeyRound, LoaderCircle, LogIn, RefreshCw, ShieldCheck } from 'lucide-react'
import { service } from './service'
import './jushuitan-settings.css'

type Settings = { account: string; has_password: boolean; connected: boolean; identity: { uid: string | number; coId: string | number; user_name?: string } | null; last_login_at: number | null; expires_at: number | null }
type Captcha = { id: string; image: string; expires_at: number }
type LoginResult = Settings | { captcha_required: true; captcha: Captcha; message: string }

export default function JushuitanSettings() {
  const [config, setConfig] = useState<Settings | null>(null)
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [passwordChanged, setPasswordChanged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [captcha, setCaptcha] = useState<Captcha | null>(null)
  const [verifyCode, setVerifyCode] = useState('')
  const [captchaLoading, setCaptchaLoading] = useState(false)
  const [captchaMessage, setCaptchaMessage] = useState('')
  const [captchaNeeded, setCaptchaNeeded] = useState(false)
  const captchaInput = useRef<HTMLInputElement>(null)
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
    event.preventDefault(); if (busy || captchaLoading || !config || captchaNeeded && (!captcha || !verifyCode.trim())) return
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await service<LoginResult>('jushuitan.login', { account: account.trim(),
        ...(captcha ? { challenge_id: captcha.id, verify_code: verifyCode.trim() } : {}),
        ...(passwordChanged || !config.has_password || account.trim() !== config.account ? { password: password || null } : {}) })
      if (!alive.current) return
      if ('captcha_required' in result) {
        setCaptchaNeeded(true); setCaptcha(result.captcha); setVerifyCode(''); setCaptchaMessage(result.message)
        requestAnimationFrame(() => { if (alive.current) captchaInput.current?.focus() })
        return
      }
      setConfig(result); setPassword(result.has_password ? '••••••••' : ''); setPasswordChanged(false)
      setCaptchaNeeded(false); setCaptcha(null); setVerifyCode(''); setCaptchaMessage('')
      setNotice('已登录聚水潭供应链，访问凭证已加密保存。')
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  async function refreshCaptcha() {
    if (busy || captchaLoading || !account.trim()) return
    setCaptchaNeeded(true); setCaptchaLoading(true); setVerifyCode(''); setCaptcha(null); setError(''); setNotice('')
    try {
      const result = await service<Captcha>('jushuitan.captcha', { account: account.trim() })
      if (alive.current) { setCaptcha(result); setCaptchaMessage('请输入图片中的验证码。') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setCaptchaLoading(false) }
  }
  return <div className="console-body">
    <form className="youzan-account-card" onSubmit={login} aria-label="聚水潭登录">
      <div className="youzan-section-title"><span className="provider-symbol"><KeyRound size={18} /></span><div><h2>聚水潭账号</h2><p>查询供应链订单，使用当前账号的渠道权限。</p></div></div>
      <div className="console-form-grid">
        <div className="console-field"><label htmlFor="jushuitan-account">账号</label><input id="jushuitan-account" autoComplete="username" maxLength={160} required disabled={busy || captchaLoading || !config} value={account} onChange={event => { setAccount(event.target.value); setPassword(''); setPasswordChanged(true); setCaptchaNeeded(false); setCaptcha(null); setVerifyCode(''); setCaptchaMessage(''); setNotice(''); setError('') }} /></div>
        <div className="console-field"><label htmlFor="jushuitan-password">密码<span>{config?.has_password && !passwordChanged ? '已保存，清空表示无需密码' : '可留空，表示无需密码'}</span></label><input id="jushuitan-password" type="password" autoComplete="new-password" maxLength={256} disabled={busy || captchaLoading || !config} value={password} onFocus={event => { if (!passwordChanged) event.currentTarget.select() }} onChange={event => { setPassword(event.target.value); setPasswordChanged(true); setNotice('') }} /></div>
      </div>
      {captchaNeeded && <section className="jushuitan-captcha" aria-label="聚水潭图形验证码">
        <div className="jushuitan-captcha-controls"><div className="console-field"><label htmlFor="jushuitan-code">图形验证码</label><input ref={captchaInput} id="jushuitan-code" value={verifyCode} disabled={busy || captchaLoading} required maxLength={64} pattern="[A-Za-z0-9]+" autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="输入图片中的字符" onChange={event => setVerifyCode(event.target.value)} /></div>
          {captcha && <button type="button" className="jushuitan-captcha-image" disabled={busy || captchaLoading} onClick={() => { void refreshCaptcha() }} aria-label="更换验证码图片" title="点击图片刷新"><img src={captcha.image} alt="聚水潭图形验证码" /></button>}
          <button type="button" className="console-ghost jushuitan-captcha-refresh" disabled={busy || captchaLoading} onClick={() => { void refreshCaptcha() }}><RefreshCw size={14} className={captchaLoading ? 'spin' : ''} />{captchaLoading ? '加载中…' : '刷新验证码'}</button></div>
        <p role="status">{captchaMessage}</p>
      </section>}
      {!captcha && !captchaLoading && error.includes('验证码') && <button type="button" className="console-ghost" disabled={busy || !account.trim()} onClick={() => { void refreshCaptcha() }}><RefreshCw size={14} />获取验证码</button>}
      <div className="youzan-account-footer"><span><ShieldCheck size={13} />凭证按员工独立加密保存</span><button className="console-primary" type="submit" disabled={busy || captchaLoading || !config || !account.trim() || captchaNeeded && (!captcha || !verifyCode.trim())}>{busy ? <LoaderCircle size={14} className="spin" /> : <LogIn size={14} />}{busy ? '正在登录…' : captchaNeeded ? '验证并登录' : config?.connected ? '重新登录' : '保存并登录'}</button></div>
    </form>
    {!config && !error && <div className="console-empty" role="status"><LoaderCircle size={20} className="spin" />正在读取配置</div>}
    <div className="console-notification" role="status" aria-live="polite">{error ? <span className="console-error">{error}</span> : notice ? <span className="console-success"><CheckCircle2 size={14} />{notice}</span> : null}</div>
    {config && <section className="console-section" aria-label="聚水潭连接状态"><div className="console-section-heading"><h2>连接状态</h2><span className={'youzan-badge' + (config.connected ? ' ready' : '')}>{config.connected ? '访问凭证已保存' : '尚未登录'}</span></div>
      <p className="console-section-description">{config.identity ? `渠道账号：${config.identity.user_name || config.account} · 公司：${config.identity.coId}` : '登录成功后，AI 将使用你自己的供应链账号查询订单。'}</p>
      {config.last_login_at && <p className="console-section-description">最近登录：{new Date(config.last_login_at * 1000).toLocaleString('zh-CN', { hour12: false })}</p>}
    </section>}
  </div>
}
