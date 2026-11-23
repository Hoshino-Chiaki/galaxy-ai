import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowUpRight, Camera, Check, KeyRound, LoaderCircle, LogOut, Pencil, UserRound, X } from 'lucide-react'
import { service } from '../service'

type Props = { user: CurrentUser; onProfileChange: (profile: PersonalProfile) => void; onLogout: () => Promise<void>; signingOut: boolean }
type Draft = Pick<PersonalProfile, 'name' | 'phone' | 'avatar'>

export default function PersonalCenter({ user, onProfileChange, onLogout, signingOut }: Props) {
  const [usage, setUsage] = useState<{today:TokenUsage;month:TokenUsage;all:TokenUsage} | null>(null)
  useEffect(() => {
    let valid = true, pending = false
    const load = async () => {
      if (pending) return
      pending = true
      try { const value = await service<{today:TokenUsage;month:TokenUsage;all:TokenUsage}>('usage.mine'); if (valid) setUsage(value) }
      catch { /* Missing usage must not block profile management. */ }
      finally { pending = false }
    }
    void load(); window.addEventListener('galaxy-usage-updated', load)
    return () => { valid = false; window.removeEventListener('galaxy-usage-updated', load) }
  }, [])
  const [profile, setProfile] = useState<PersonalProfile | null>(null)
  const [mode, setMode] = useState<'overview' | 'edit' | 'password'>('overview')
  const [draft, setDraft] = useState<Draft>({ name: '', phone: '', avatar: null })
  const [passwords, setPasswords] = useState({ current: '', next: '', confirm: '' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [retry, setRetry] = useState(0)
  const alive = useRef(true)
  const imageInput = useRef<HTMLInputElement>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    let valid = true
    setLoading(true); setError('')
    void service<PersonalProfile>('profile.get').then(value => {
      if (valid) setProfile(value)
    }).catch(reason => { if (valid) setError((reason as Error).message) })
      .finally(() => { if (valid) setLoading(false) })
    return () => { valid = false }
  }, [user.id, retry])
  function changeMode(next: typeof mode) {
    setError(''); setNotice(''); setPasswords({ current: '', next: '', confirm: '' })
    if (next === 'edit' && profile) setDraft({ name: profile.name || user.username, phone: profile.phone, avatar: profile.avatar })
    setMode(next)
  }
  async function selectAvatar(file?: File) {
    if (!file) return
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) {
      setError('请选择 5 MB 以内的 PNG、JPEG 或 WebP 图片。'); return
    }
    setProcessing(true)
    const source = URL.createObjectURL(file)
    try {
      const image = new Image(); image.src = source; await image.decode()
      const canvas = document.createElement('canvas'); canvas.width = 128; canvas.height = 128
      const size = Math.min(image.naturalWidth, image.naturalHeight)
      canvas.getContext('2d')!.drawImage(image, (image.naturalWidth - size) / 2, (image.naturalHeight - size) / 2, size, size, 0, 0, 128, 128)
      if (alive.current) { setDraft(value => ({ ...value, avatar: canvas.toDataURL('image/webp', .85) })); setError('') }
    } catch { if (alive.current) setError('图片无法读取，请换一张图片。') }
    finally { URL.revokeObjectURL(source); if (imageInput.current) imageInput.current.value = ''; if (alive.current) setProcessing(false) }
  }
  async function saveProfile(event: FormEvent) {
    event.preventDefault(); if (saving || processing) return
    setSaving(true); setError(''); setNotice('')
    try {
      const value = await service<PersonalProfile>('profile.save', draft)
      if (alive.current) { setProfile(value); onProfileChange(value); setMode('overview'); setNotice('个人资料已更新') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }
  async function savePassword(event: FormEvent) {
    event.preventDefault(); if (saving) return
    if (passwords.next !== passwords.confirm) { setError('两次输入的新密码不一致。'); return }
    setSaving(true); setError(''); setNotice('')
    try {
      await service('profile.password', { current_password: passwords.current, new_password: passwords.next })
      if (alive.current) { setPasswords({ current: '', next: '', confirm: '' }); setMode('overview'); setNotice('密码已修改，其他登录会话已失效。') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }
  const avatar = mode === 'edit' ? draft.avatar : profile?.avatar ?? user.avatar
  const name = profile?.name || user.name || user.username
  const busy = saving || processing
  return <aside className="personal-center" aria-label="个人中心">
    <div className="personal-scroll">
      <div className="personal-identity">
        <div className="personal-avatar">{avatar ? <img src={avatar} alt="个人头像" /> : <UserRound size={28} strokeWidth={1.3} />}</div>
        <strong title={name}>{name}</strong><span className="personal-role"><span />{user.role === 'admin' ? '管理员' : '员工'}</span>
      </div>
      {mode === 'overview' ? <>
        <div className="personal-section-heading"><span>账户资料</span><button type="button" className="personal-icon-button" title="编辑个人资料" aria-label="编辑个人资料" disabled={loading || !profile} onClick={() => changeMode('edit')}><Pencil size={14} /></button></div>
        {loading ? <div className="personal-loading" role="status"><LoaderCircle className="spin" size={16} />正在读取资料</div> : profile && <dl className="personal-details">
          {([['账户', profile.account], ['姓名', profile.name], ['手机', profile.phone], ['部门', profile.department], ['岗位', profile.job_title], ['在职状态', profile.employment_status === 'active' ? '在职' : '离职']] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd title={value}>{value || '未填写'}</dd></div>)}
        </dl>}
        <div className="personal-actions"><button type="button" disabled={loading || !profile} onClick={() => changeMode('edit')}><Pencil size={15} /><span>编辑资料</span><ArrowUpRight size={14} /></button><button type="button" onClick={() => changeMode('password')}><KeyRound size={15} /><span>修改密码</span><ArrowUpRight size={14} /></button></div>
      </> : <form className="personal-form" onSubmit={mode === 'edit' ? saveProfile : savePassword}>
        <div className="personal-form-heading"><button type="button" className="personal-icon-button" aria-label="返回个人资料" disabled={busy} onClick={() => changeMode('overview')}><ArrowLeft size={16} /></button><span>{mode === 'edit' ? '编辑资料' : '修改密码'}</span></div>
        {mode === 'edit' ? <>
          <div className="personal-avatar-actions"><input ref={imageInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={event => { void selectAvatar(event.target.files?.[0]) }} /><button type="button" disabled={busy} onClick={() => imageInput.current?.click()}><Camera size={14} />{processing ? '正在处理…' : '上传头像'}</button>{draft.avatar && <button type="button" className="personal-icon-button" title="移除头像" aria-label="移除头像" disabled={busy} onClick={() => setDraft(value => ({ ...value, avatar: null }))}><X size={14} /></button>}</div>
          <label><span>姓名</span><input name="name" autoComplete="name" required maxLength={80} disabled={busy} value={draft.name} onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
          <label><span>手机号</span><input name="phone" type="tel" autoComplete="tel" maxLength={32} disabled={busy} placeholder="填写手机号" value={draft.phone} onChange={event => setDraft({ ...draft, phone: event.target.value })} /></label>
        </> : <>
          <label><span>当前密码</span><input type="password" autoComplete="current-password" required maxLength={256} disabled={busy} value={passwords.current} onChange={event => setPasswords({ ...passwords, current: event.target.value })} /></label>
          <label><span>新密码</span><input type="password" autoComplete="new-password" required minLength={8} maxLength={256} disabled={busy} placeholder="至少 8 位" value={passwords.next} onChange={event => setPasswords({ ...passwords, next: event.target.value })} /></label>
          <label><span>确认新密码</span><input type="password" autoComplete="new-password" required minLength={8} maxLength={256} disabled={busy} value={passwords.confirm} onChange={event => setPasswords({ ...passwords, confirm: event.target.value })} /></label>
        </>}
        <button type="submit" className="personal-save" disabled={busy}>{saving ? <LoaderCircle size={15} className="spin" /> : <Check size={15} />}{saving ? '正在保存…' : '保存修改'}</button>
      </form>}
      {mode === 'overview' && usage && <div className="personal-usage"><span>我的 AI 用量 · Token</span>{([['today','今日'],['month','本月'],['all','累计']] as const).map(([key,label]) => <div key={key}><span>{label}</span><strong>{usage[key].unknown_requests ? '≥ ' : ''}{usage[key].total_tokens.toLocaleString()}</strong></div>)}<small>按账号统计，含桌面、微信、飞书和定时任务。</small></div>}
      {error && <div className="personal-feedback error" role="alert">{error}{!profile && mode === 'overview' && <button type="button" disabled={loading} onClick={() => setRetry(value => value + 1)}>重试</button>}</div>}
      {notice && <div className="personal-feedback success" role="status"><Check size={14} />{notice}</div>}
    </div>
    <footer className="personal-footer"><button type="button" disabled={signingOut || busy} onClick={() => { void onLogout() }}><LogOut size={15} /><span>{signingOut ? '正在退出…' : '退出登录'}</span></button></footer>
  </aside>
}
