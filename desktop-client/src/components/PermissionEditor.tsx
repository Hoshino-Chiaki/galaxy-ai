import { useEffect, useState } from 'react'
import { ArrowLeft, Check, LoaderCircle, Search, ShieldCheck } from 'lucide-react'
import { service } from '../service'
import './permissions.css'
export type PermissionTarget = { kind: 'department' | 'user'; id: string; name: string }
type Snapshot = { target: PermissionTarget; revision: number; effects: Record<string, boolean>; inherited: string[]; effective: string[]; protected: string[]; management?: boolean; inherited_source?: string; categories: { id: string; name: string; permissions: { code: string; name: string; action: string }[] }[] }
export default function PermissionEditor({ target, onBack }: { target: PermissionTarget; onBack: () => void }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [effects, setEffects] = useState<Record<string, boolean>>({})
  const [category, setCategory] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [management, setManagement] = useState(false)
  useEffect(() => {
    let valid = true
    void service<Snapshot>('permissions.get', target).then(value => {
      if (valid) { setSnapshot(value); setEffects(value.effects); setManagement(value.management === true); setCategory(value.categories[0]?.id || '') }
    }).catch(reason => { if (valid) setError((reason as Error).message) })
    return () => { valid = false }
  }, [target.id, target.kind])
  const changed = snapshot ? new Set([...Object.keys(effects), ...Object.keys(snapshot.effects)].filter(code => effects[code] !== snapshot.effects[code])).size + Number(management !== (snapshot.management === true)) : 0
  const inherited = new Set(snapshot?.inherited || [])
  const isDepartment = target.kind === 'department'
  const enabled = (code: string) => management || (effects[code] ?? inherited.has(code))
  function setMode(codes: string[], mode: 'allow' | 'deny') {
    setSaved(false)
    setEffects(current => { const next = { ...current }; for (const code of codes) { if (management) continue; const allow = mode === 'allow'; if (!isDepartment && allow === inherited.has(code)) delete next[code]; else next[code] = allow } return next })
  }
  async function save() {
    if (!snapshot || saving || !changed) return
    setSaving(true); setError(''); setSaved(false)
    try {
      const value = await service<Snapshot>('permissions.save', { ...target, revision: snapshot.revision, effects, ...(!isDepartment ? { management } : {}) })
      setSnapshot(value); setEffects(value.effects); setManagement(value.management === true); setSaved(true)
      window.dispatchEvent(new Event('galaxy:permissions-changed'))
    } catch (reason) { setError((reason as Error).message) }
    finally { setSaving(false) }
  }
  const current = snapshot?.categories.find(value => value.id === category)
  const visible = current?.permissions.filter(item => (item.name + item.code).toLowerCase().includes(query.toLowerCase())) || []
  function back() { if (!changed || window.confirm('权限修改尚未保存，确定返回？')) onBack() }
  return <div className="permission-editor">
    <div className="personnel-view-heading"><div><button type="button" className="personnel-back" onClick={back}><ArrowLeft size={15} />返回{target.kind === 'department' ? '部门' : '人员'}</button><h2>{target.name}<span className="permission-heading-label">权限</span></h2></div><ShieldCheck size={22} className="permission-heading-icon" /></div>
    <p className="permission-intro">{target.kind === 'department' ? '为部门明确设置允许或不允许，成员默认使用这些权限。' : management ? '管理员权限已开启，拥有全部功能权限；关闭后按部门和个人设置生效。' : '默认继承所在部门；单独关闭某项权限，仅对这个人生效。'}渠道拥有任意功能时，自动显示对应账号登录入口。</p>
    {error && <p className="console-error" role="alert">{error}</p>}
    {!isDepartment && snapshot && <label className="permission-admin-toggle"><input type="checkbox" checked={management} disabled={saving} onChange={event => { setManagement(event.target.checked); setSaved(false) }} /><span><strong>管理员权限</strong><small>勾选后拥有全部功能权限</small></span></label>}
    {!snapshot ? !error && <div className="personnel-grid-empty" role="status"><LoaderCircle className="spin" size={20} />正在读取权限</div> : <>
      <nav className="permission-channels" aria-label="权限渠道">{snapshot.categories.map(value => <button type="button" key={value.id} aria-pressed={category === value.id} className={category === value.id ? 'selected' : ''} onClick={() => { setCategory(value.id); setQuery('') }}><span>{value.name}</span><small>{value.permissions.filter(item => enabled(item.code)).length}/{value.permissions.length}</small></button>)}</nav>
      <div className="permission-list-heading"><label className="permission-search"><Search size={15} /><input aria-label="搜索权限" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索当前渠道的功能" /></label><div className="permission-bulk">{(['allow', 'deny'] as const).map((mode, index) => <button type="button" key={mode} disabled={management || saving} onClick={() => setMode(visible.map(item => item.code), mode)}>{['全部允许', '全部不允许'][index]}</button>)}</div></div>
      <div className="permission-list">{['新增', '删除', '修改', '查询', '其他'].filter(action => visible.some(item => item.action === action)).map(action => <section className="permission-action-group" key={action} aria-label={action + '权限'}><h3>{action}<small>{visible.filter(item => item.action === action).length} 项</small></h3>{visible.filter(item => item.action === action).map(item => {
        const fromDepartment = !isDepartment && effects[item.code] === undefined
        const mode = enabled(item.code) ? 'allow' : 'deny'
        const locked = management
        return <div className="permission-item" key={item.code}><div><strong>{item.name}</strong><span className={enabled(item.code) ? 'permission-state allowed' : 'permission-state'}><i />{enabled(item.code) ? '允许' : '不允许'}<small>{locked ? '管理员权限' : isDepartment ? '部门设置' : fromDepartment ? snapshot.inherited_source && snapshot.inherited_source !== '账号默认' ? '继承自' + snapshot.inherited_source : '账号默认' : '个人设置'}</small></span></div><div className="permission-modes" role="group" aria-label={item.name}>{(['allow', 'deny'] as const).map((value, index) => <button key={value} data-mode={value} type="button" disabled={locked || saving} aria-pressed={mode === value} onClick={() => setMode([item.code], value)}>{['允许', '不允许'][index]}</button>)}</div></div>
      })}</section>)}{!visible.length && <div className="permission-empty">没有匹配的功能</div>}</div>
      <footer className="permission-footer"><span role="status">{saved ? <><Check size={15} />已保存，权限立即生效</> : changed ? `${changed} 项修改尚未保存` : isDepartment ? '保存后作为部门成员的默认权限' : '与部门一致时自动恢复继承'}</span><button type="button" className="console-primary" disabled={saving || !changed} onClick={() => { void save() }}>{saving && <LoaderCircle size={14} className="spin" />}保存权限</button></footer>
    </>}
  </div>
}
