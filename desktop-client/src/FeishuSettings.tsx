import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, ExternalLink, KeyRound, Link2, LoaderCircle, LogOut, RefreshCw, Save, ShieldCheck } from 'lucide-react'
import { service } from './service'
import { copyText } from './clipboard'

type Config = { configured: boolean; app_id: string; has_app_secret?: boolean; connected: boolean; cli_ready: boolean;
  identity: { name: string; open_id: string; tenant_key: string } | null; expires_at: number | null; refresh_expires_at: number | null;
  scopes: string[]; required_scopes: string[];
  authorization_mode?: 'all'; granted_scope_count?: number;
  permission_import?: { scopes: { user: string[] } }; business_domains?: number; business_operations?: number;
  pending: { id: string; url: string; user_code: string; expires_at: number; interval: number } | null }
const date = (value?: number | null) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'

export default function FeishuSettings({ user }: { user: CurrentUser }) {
  const [config, setConfig] = useState<Config | null>(null)
  const [appId, setAppId] = useState(''), [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const alive = useRef(false), polling = useRef(false)
  const admin = user.permissions.includes('feishu:app:configure')
  useEffect(() => {
    let valid = true; alive.current = true
    void service<Config>('feishu.settings').then(value => { if (valid) { setConfig(value); setAppId(value.app_id) } }).catch(reason => { if (valid) setError(reason.message) })
    return () => { valid = false; alive.current = false }
  }, [])
  useEffect(() => {
    if (!config?.pending) return
    const pending = config.pending
    const timer = window.setInterval(() => {
      if (polling.current) return
      if (pending.expires_at * 1000 <= Date.now()) { setError('授权链接已过期，请重新发起授权。'); window.clearInterval(timer); return }
      polling.current = true
      void service<Config>('feishu.poll', { id: pending.id }).then(value => {
        if (alive.current) { setConfig(value); if (!value.pending && value.connected) setNotice('飞书账号已连接。') }
      }).catch(reason => { if (alive.current) setError(reason.message); window.clearInterval(timer) }).finally(() => { polling.current = false })
    }, Math.max(1, pending.interval) * 1000)
    return () => window.clearInterval(timer)
  }, [config?.pending?.id, config?.pending?.interval])
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return
    setBusy(true); setError(''); setNotice('')
    try { const next = await service<Config>('feishu.saveApp', { app_id: appId.trim(), app_secret: secret }); if (alive.current) { setConfig(next); setSecret(''); setNotice('企业应用已保存，成员可各自授权。') } }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  async function authorize() {
    if (busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      const next = await service<Config>('feishu.authorize')
      if (alive.current) { setConfig(next); if (next.pending) await window.desktop?.openExternal(next.pending.url) }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  async function disconnect() {
    if (busy || !window.confirm('断开当前账号的飞书连接？其他成员的授权不受影响。')) return
    setBusy(true); setError(''); setNotice('')
    try { const next = await service<Config>('feishu.disconnect'); if (alive.current) { setConfig(next); setNotice('已断开当前账号的连接。') } }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy(false) }
  }
  return <div className="console-body feishu-settings">
    <div className="console-page-heading"><div><h1>飞书</h1><p>连接自己的飞书账号，按授权范围使用文档、表格、消息等业务能力。</p></div><span className="console-status"><span className="status-dot" />{config?.business_operations ? `${config.business_domains} 个业务域 · ${config.business_operations} 个操作` : '个人授权'}</span></div>
    <div className="console-notification" role="status">{error ? <span className="console-error">{error}</span> : notice ? <span className="console-success"><CheckCircle2 size={14} />{notice}</span> : null}</div>
    {!config ? <div className="console-empty"><LoaderCircle className="spin" /><p>{error ? '无法读取飞书连接' : '正在读取连接'}</p></div> : <>
      {admin && <form className="youzan-account-card" onSubmit={save}>
        <div className="youzan-section-title"><span className="provider-symbol"><KeyRound size={18} /></span><div><h2>企业应用 · OAuth</h2><p>管理员统一配置，成员分别授权自己的飞书账号。</p></div></div>
        <div className="console-form-grid">
          <div className="console-field"><label htmlFor="feishu-app-id">App ID</label><input id="feishu-app-id" value={appId} disabled={busy} placeholder="cli_xxxxxxxxxx" required maxLength={104} onChange={event => setAppId(event.target.value)} /></div>
          <div className="console-field"><label htmlFor="feishu-app-secret">App Secret{config.has_app_secret && <span>留空保持原密钥</span>}</label><input id="feishu-app-secret" type="password" autoComplete="new-password" value={secret} disabled={busy} required={!config.has_app_secret || appId !== config.app_id} maxLength={512} placeholder={config.has_app_secret ? '已加密保存' : '输入应用密钥'} onChange={event => setSecret(event.target.value)} /></div>
        </div>
        <div className="youzan-account-footer"><span><ShieldCheck size={13} />密钥只保存在服务器，不向成员展示</span><button className="console-primary" disabled={busy || !appId.trim()}><Save size={14} />保存应用</button></div>
        <p className="console-section-description">管理员在飞书开放平台一次性开通用户身份权限并发布，员工只需连接自己的账号。服务器统一申请全部已接入权限，自动使用和刷新实际获得的授权。</p>
        <details className="feishu-scopes"><summary>全部业务权限 · 飞书批量导入 JSON</summary><pre>{JSON.stringify(config.permission_import || { scopes: { user: config.required_scopes } }, null, 2)}</pre><button type="button" className="console-ghost" onClick={() => { void copyText(JSON.stringify(config.permission_import || { scopes: { user: config.required_scopes } }, null, 2)).then(() => setNotice('权限 JSON 已复制。')).catch(() => setError('复制失败，请重试。')) }}>复制权限 JSON</button></details>
      </form>}
      <section className="youzan-account-card feishu-connection">
        <div className="youzan-section-title"><span className="provider-symbol"><Link2 size={18} /></span><div><h2>我的飞书账号</h2><p>{config.connected ? '已连接 · ' + (config.identity?.name || '飞书用户') : config.configured ? '企业应用已配置，授权后即可使用' : '请等待管理员配置企业应用'}</p></div><span className={'youzan-badge' + (config.connected ? ' ready' : '')}>{config.connected ? '已授权' : '未授权'}</span></div>
        <p className="console-section-description">AI 自动使用你已授权且有权访问的全部能力，服务器自动刷新授权，无需按业务选择范围。飞书要求重新确认时，点击补全授权即可。</p>
        {admin && config.connected && <details className="feishu-scopes"><summary>授权详情 · {config.granted_scope_count ?? config.scopes.length} 项实际权限</summary><dl className="feishu-identity"><div><dt>当前凭证有效期</dt><dd>{date(config.expires_at)}</dd></div><div><dt>续期凭证有效期</dt><dd>{date(config.refresh_expires_at)}</dd></div></dl><pre>{config.scopes.join('\n')}</pre></details>}
        {!config.cli_ready && <p className="console-error">服务器飞书 CLI 尚未准备好。</p>}
        {config.pending && <div className="feishu-pending" role="status"><p><LoaderCircle size={14} className="spin" />等待你在飞书确认授权</p>{config.pending.user_code && <strong>{config.pending.user_code}</strong>}<button type="button" className="console-ghost" onClick={() => { void window.desktop?.openExternal(config.pending!.url) }}><ExternalLink size={13} />打开授权页面</button><small>有效至 {date(config.pending.expires_at)}</small></div>}
        <div className="youzan-account-footer"><span><ShieldCheck size={13} />每位成员独立授权，按自己的权限读取或修改</span><div className="feishu-actions">
          {config.connected && <button type="button" className="console-ghost" disabled={busy} onClick={() => { void disconnect() }}><LogOut size={13} />断开连接</button>}
          <button type="button" className="console-primary" disabled={busy || !config.configured || !config.cli_ready} onClick={() => { void authorize() }}>{busy ? <LoaderCircle size={14} className="spin" /> : config.connected ? <RefreshCw size={14} /> : <ExternalLink size={14} />}{config.connected ? '补全授权' : '连接飞书'}</button>
        </div></div>
      </section>
    </>}
  </div>
}
