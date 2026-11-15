import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowRight, Check, Eye, EyeOff, LoaderCircle,
  LockKeyhole, Maximize2, Minus, Pause, Play, UserRound, X } from 'lucide-react'
import Workspace from './Workspace'

const preferenceKey = 'xingxu.login.preferences.v1'

async function readPreferences(): Promise<LoginPreferences> {
  if (window.desktop) return window.desktop.preferences.read()
  try {
    const stored = JSON.parse(localStorage.getItem(preferenceKey) || '{}')
    return { remember: stored.remember === true,
      account: stored.remember === true && typeof stored.account === 'string' ? stored.account : '' }
  } catch { return { remember: false, account: '' } }
}

async function writePreferences(value: LoginPreferences) {
  const preferences = { remember: value.remember, account: value.remember ? value.account.trim() : '' }
  if (window.desktop) return window.desktop.preferences.write(preferences)
  localStorage.setItem(preferenceKey, JSON.stringify(preferences))
}

export default function App() {
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [ready, setReady] = useState(false)
  const [session, setSession] = useState<Session | null>(null)
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [fieldError, setFieldError] = useState<'account' | 'password' | null>(null)
  const [paused, setPaused] = useState(false)
  const [videoUnavailable, setVideoUnavailable] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const accountRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    async function initialize() {
      try {
        const value = await readPreferences()
        if (mounted.current) { setAccount(value.account); setRemember(value.remember) }
        if (window.desktop) {
          const result = await window.desktop.auth.current()
          if (mounted.current) {
            if (result.ok) setSession(result.data)
            else setError(result.message)
          }
        }
      } catch { if (mounted.current) setError('无法恢复登录，请稍后重试。') }
      finally { if (mounted.current) setReady(true) }
    }
    void initialize()
    return () => { mounted.current = false }
  }, [])

  useEffect(() => {
    if (!ready) return
    const timeout = setTimeout(() => {
      writePreferences({ account, remember }).catch(() => {
        if (mounted.current) setError('无法保存登录偏好，请检查本地存储权限。')
      })
    }, 250)
    return () => clearTimeout(timeout)
  }, [account, remember, ready])

  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (reduced.matches) { video.current?.pause(); setPaused(true) }
  }, [])

  const userId = session?.user.id
  useEffect(() => {
    if (!userId || !window.desktop) return
    let active = true
    async function revalidate() {
      const result = await window.desktop!.auth.current().catch(() => null)
      if (active && result?.ok) {
        setSession(result.data)
        if (!result.data) setError('登录已失效，请重新登录。')
      }
    }
    const interval = setInterval(() => { void revalidate() }, 60000)
    window.addEventListener('focus', revalidate)
    return () => { active = false; clearInterval(interval); window.removeEventListener('focus', revalidate) }
  }, [userId])

  useEffect(() => {
    document.title = session ? 'galaxy ai' : 'galaxy ai · 登录'
  }, [userId])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (submitting || !ready) return
    setError(''); setFieldError(null)
    if (!account.trim()) {
      setFieldError('account'); setError('请输入账号。'); accountRef.current?.focus(); return
    }
    if (!password) {
      setFieldError('password'); setError('请输入密码。'); passwordRef.current?.focus(); return
    }
    setSubmitting(true)
    try {
      await writePreferences({ account, remember })
      if (!window.desktop) { setError('请在 Windows 客户端中登录。'); return }
      const result = await window.desktop.auth.login({ username: account.trim(), password, remember })
      if (result.ok && result.data) { setSession(result.data); setPassword(''); setShowPassword(false) }
      else if (!result.ok) setError(result.message)
    } catch { setError('无法连接登录服务，请稍后再试。') }
    finally { if (mounted.current) setSubmitting(false) }
  }

  async function toggleVideo() {
    if (!video.current) return
    if (paused) {
      try { await video.current.play(); setPaused(false) } catch { setVideoUnavailable(true) }
    } else { video.current.pause(); setPaused(true) }
  }

  async function logout() {
    if (!window.desktop || submitting) return
    setSubmitting(true); setError('')
    try {
      const result = await window.desktop.auth.logout()
      if (result.ok) { setSession(null); setPassword(''); setShowPassword(false) }
      else setError(result.message)
    } catch { setError('退出失败，请稍后重试。') }
    finally { if (mounted.current) setSubmitting(false) }
  }

  return <div className={`app-shell${session ? ' authenticated' : ''}`}>
    {!session && <div className="scene" aria-hidden="true">
      <video ref={video} className={videoUnavailable ? 'video-hidden' : ''} autoPlay muted loop playsInline
        preload="auto" poster="./bg-poster.jpg" onError={() => setVideoUnavailable(true)}>
        <source src="./bg.mp4" type="video/mp4" />
      </video>
      <div className="scene-shade" /><div className="scene-grain" />
    </div>}

    <header className="titlebar">
      {window.desktop && <div className="window-controls">
        <button type="button" aria-label="最小化" onClick={() => window.desktop?.minimize()}><Minus size={15} /></button>
        <button type="button" aria-label="最大化或还原" onClick={() => window.desktop?.maximize()}><Maximize2 size={12} /></button>
        <button type="button" className="window-close" aria-label="关闭窗口" onClick={() => window.desktop?.close()}><X size={16} /></button>
      </div>}
    </header>

    {session ? <Workspace key={session.user.id} user={session.user} onLogout={logout} signingOut={submitting} error={error} /> : <div className="page">
      <main className="login-layout">
        <section className="login-card" aria-labelledby="login-heading">
          <div className="login-brand">galaxy ai</div>
          <h1 id="login-heading">登录</h1>
          <form onSubmit={submit} noValidate>
            <div className="field-group"><label className="sr-only" htmlFor="account">账号</label>
              <div className={`input-wrap ${fieldError === 'account' ? 'invalid' : ''}`}><UserRound size={17} strokeWidth={1.6} />
                <input ref={accountRef} id="account" name="account" autoComplete="username" placeholder="账号"
                  value={account} maxLength={160} disabled={!ready || submitting} aria-invalid={fieldError === 'account'}
                  aria-describedby={error ? 'login-feedback' : undefined}
                  onChange={e => { setAccount(e.target.value); setError(''); setFieldError(null) }} /></div>
            </div>
            <div className="field-group"><label className="sr-only" htmlFor="password">密码</label>
              <div className={`input-wrap ${fieldError === 'password' ? 'invalid' : ''}`}><LockKeyhole size={17} strokeWidth={1.6} />
                <input ref={passwordRef} id="password" name="password" type={showPassword ? 'text' : 'password'} autoComplete="current-password"
                  placeholder="密码" value={password} maxLength={256} disabled={submitting}
                  aria-invalid={fieldError === 'password'} aria-describedby={error ? 'login-feedback' : undefined}
                  onChange={e => { setPassword(e.target.value); setError(''); setFieldError(null) }} />
                <button type="button" className="show-password" aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  aria-pressed={showPassword} onClick={() => setShowPassword(value => !value)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button>
              </div>
            </div>
            <div className="form-options"><label className="remember"><input type="checkbox" name="remember" checked={remember} disabled={!ready || submitting}
              onChange={e => setRemember(e.target.checked)} /><span className="checkbox-mark"><Check size={11} strokeWidth={3} /></span><span>记住登录</span></label></div>
            <button className="login-button" type="submit" disabled={submitting || !ready}>
              <span>{submitting ? '正在连接' : '登 录'}</span>{submitting ? <LoaderCircle className="spin" size={18} /> : <ArrowRight size={19} />}
            </button>
            <div id="login-feedback" className="feedback" role="status" aria-live="polite">{error}</div>
          </form>
        </section>
      </main>

      <button type="button" className="video-control" onClick={toggleVideo} disabled={videoUnavailable}
            aria-label={paused ? '播放背景视频' : '暂停背景视频'} title={paused ? '播放背景视频' : '暂停背景视频'}>
            {paused ? <Play size={12} /> : <Pause size={12} />}</button>
    </div>}
  </div>
}
