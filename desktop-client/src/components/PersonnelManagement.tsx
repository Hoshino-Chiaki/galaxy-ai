import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, Building2, ChevronLeft, ChevronRight, LoaderCircle, Pencil, Plus, RefreshCw, Save, Search, Trash2, Upload, UserRound, Users, X } from 'lucide-react'
import Nav from 'react-bootstrap/Nav'
import { service } from '../service'
import './personnel.css'
import PermissionEditor, { type PermissionTarget } from './PermissionEditor'

type Props = { user: CurrentUser }
type Form = EmployeeRecord & { password: string }
type DepartmentForm = { id: string | null; name: string }
function cardTone(name: string) {
  if (/总裁|CEO/i.test(name)) return 'tone-amber'
  if (name.includes('新零售')) return 'tone-cyan'
  const hash = Array.from(name).reduce((value, char) => (value * 31 + char.charCodeAt(0)) >>> 0, 0)
  return ['tone-blue', 'tone-teal', 'tone-cyan', 'tone-rose'][hash % 4]
}

export default function PersonnelManagement({ user }: Props) {
  const allowed = user.permissions.includes('users:manage')
  const canManagePermissions = user.permissions.includes('permissions:manage')
  const [permissionTarget, setPermissionTarget] = useState<PermissionTarget | null>(null)
  const [departments, setDepartments] = useState<DepartmentRecord[]>([])
  const [department, setDepartment] = useState<DepartmentRecord | null>(null)
  const [departmentQuery, setDepartmentQuery] = useState('')
  const [departmentLoading, setDepartmentLoading] = useState(true)
  const [departmentError, setDepartmentError] = useState('')
  const [departmentForm, setDepartmentForm] = useState<DepartmentForm | null>(null)
  const [departmentSaving, setDepartmentSaving] = useState(false)
  const [departmentFormError, setDepartmentFormError] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<DepartmentRecord | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [selected, setSelected] = useState<EmployeeRecord | null>(null)
  const [rows, setRows] = useState<EmployeeRecord[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('')
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState('')
  const [form, setForm] = useState<Form | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const alive = useRef(true)
  const dialog = useRef<HTMLDialogElement>(null)
  const departmentDialog = useRef<HTMLDialogElement>(null)
  const deleteDialog = useRef<HTMLDialogElement>(null)
  const avatarInput = useRef<HTMLInputElement>(null)
  const viewHeading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  useEffect(() => {
    if (!allowed) return
    let valid = true
    setDepartmentLoading(true); setDepartmentError('')
    void service<{ departments: DepartmentRecord[] }>('erp.departments').then(value => {
      if (valid) setDepartments(value.departments)
    }).catch(reason => { if (valid) setDepartmentError((reason as Error).message) })
      .finally(() => { if (valid) setDepartmentLoading(false) })
    return () => { valid = false }
  }, [allowed, refresh])
  const departmentName = department ? department.id ? department.name : '' : undefined
  useEffect(() => {
    if (!allowed || departmentName === undefined) return
    let valid = true
    setLoading(true); setNotice(''); setRows([])
    void service<{ employees: EmployeeRecord[]; total: number }>('erp.employees', { q: filter, status, page, department: departmentName }).then(value => {
      if (valid) { setRows(value.employees); setTotal(value.total) }
    }).catch(reason => { if (valid) setNotice((reason as Error).message) })
      .finally(() => { if (valid) setLoading(false) })
    return () => { valid = false }
  }, [allowed, departmentName, filter, status, page, refresh])
  useEffect(() => { viewHeading.current?.focus() }, [departmentName, selected?.id])
  useEffect(() => { if (form) dialog.current?.showModal(); else dialog.current?.close() }, [!!form])
  useEffect(() => { if (departmentForm) departmentDialog.current?.showModal(); else departmentDialog.current?.close() }, [!!departmentForm])
  useEffect(() => { if (deleteTarget) deleteDialog.current?.showModal(); else deleteDialog.current?.close() }, [!!deleteTarget])
  function openDepartment(value: DepartmentRecord) {
    setDepartment(value); setSelected(null); setQuery(''); setFilter(''); setPage(1); setStatus(''); setRows([]); setTotal(value.employee_count)
  }
  function allDepartments() { setSelected(null); setDepartment(null) }
  function editDepartment(value?: DepartmentRecord) {
    setDepartmentFormError(''); setDepartmentForm(value ? { id: value.id, name: value.name } : { id: null, name: '' })
  }
  async function saveDepartment(event: FormEvent) {
    event.preventDefault(); if (!departmentForm || departmentSaving) return
    setDepartmentSaving(true); setDepartmentFormError('')
    try {
      const value = await service<DepartmentRecord>('erp.saveDepartment', departmentForm)
      if (!alive.current) return
      if (department?.id === value.id) setDepartment(value)
      setDepartmentForm(null); setRefresh(value => value + 1)
    } catch (reason) { if (alive.current) setDepartmentFormError((reason as Error).message) }
    finally { if (alive.current) setDepartmentSaving(false) }
  }
  async function deleteDepartment(event: FormEvent) {
    event.preventDefault(); if (!deleteTarget?.id || deleting) return
    setDeleting(true); setDeleteError('')
    try {
      await service('erp.deleteDepartment', { id: deleteTarget.id })
      if (alive.current) { setDeleteTarget(null); allDepartments(); setRefresh(value => value + 1) }
    } catch (reason) { if (alive.current) setDeleteError((reason as Error).message) }
    finally { if (alive.current) setDeleting(false) }
  }
  async function save(event: FormEvent) {
    event.preventDefault(); if (!form || saving) return
    setSaving(true); setError('')
    try {
      const { id, user_id: _user, linked_accounts: _links, ...employee } = form
      const value = await service<EmployeeRecord>('erp.saveEmployee', { id, employee: { ...employee, password: employee.password || null } })
      if (alive.current) {
        setForm(null); setSelected(value)
        if (value.department !== departmentName) setDepartment(departments.find(item => item.id ? item.name === value.department : !value.department) || { id: null, name: '未分配部门', employee_count: 0 })
        setRefresh(value => value + 1)
      }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }
  async function avatar(file?: File) {
    if (!file || !form) return
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) { setError('请选择 5 MB 以内的 PNG、JPEG 或 WebP 图片。'); return }
    const source = URL.createObjectURL(file)
    try {
      const image = new Image(); image.src = source; await image.decode()
      const canvas = document.createElement('canvas'); canvas.width = 128; canvas.height = 128
      const size = Math.min(image.naturalWidth, image.naturalHeight)
      canvas.getContext('2d')!.drawImage(image, (image.naturalWidth - size) / 2, (image.naturalHeight - size) / 2, size, size, 0, 0, 128, 128)
      if (alive.current) { setForm(current => current ? { ...current, avatar: canvas.toDataURL('image/webp', .85) } : current); setError('') }
    } catch { if (alive.current) setError('图片无法读取，请换一张图片。') }
    finally { URL.revokeObjectURL(source); if (avatarInput.current) avatarInput.current.value = '' }
  }
  function edit(row: EmployeeRecord) { setError(''); setForm({ ...row, password: '' }) }
  const visibleDepartments = departments.filter(value => value.name.toLocaleLowerCase().includes(departmentQuery.trim().toLocaleLowerCase()))
  return <section className="console-panel personnel-panel" aria-label="ERP 人员管理">
    <div className="console-scroll"><div className="console-body personnel-body">
      {permissionTarget ? <PermissionEditor key={permissionTarget.kind + permissionTarget.id} target={permissionTarget} onBack={() => setPermissionTarget(null)} /> : !allowed ? <div className="console-empty"><p>当前账号没有人员管理权限。</p></div> : !department ? <>
        <div className="personnel-view-heading"><div><h2 ref={viewHeading} tabIndex={-1}>部门</h2><span>{departments.filter(value => value.id).length} 个部门 · {departments.reduce((count, value) => count + value.employee_count, 0)} 位员工</span></div><button type="button" className="console-primary" onClick={() => editDepartment()}><Plus size={14} />创建部门</button></div>
        <div className="personnel-toolbar"><label className="personnel-search department-search"><Search size={15} aria-hidden="true" /><input aria-label="搜索部门" placeholder="搜索部门" value={departmentQuery} onChange={event => setDepartmentQuery(event.target.value)} maxLength={100} /></label><button type="button" className="console-ghost" disabled={departmentLoading} aria-label="刷新部门" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={14} className={departmentLoading ? 'spin' : ''} /></button></div>
        {departmentError && <p className="console-error" role="alert">{departmentError}</p>}
        <div className="personnel-card-grid department-grid" aria-label="部门卡片" aria-busy={departmentLoading}>
          {departmentLoading ? <div className="personnel-grid-empty" role="status"><LoaderCircle size={20} className="spin" />正在读取部门</div> : !departmentError && visibleDepartments.map(value => <Nav key={value.id || 'unassigned'} fill className={'employee-row department-row ' + cardTone(value.name)} aria-label={`${value.name}的人员与权限`}>
            <div className="employee-row-identity department-row-identity"><span className="department-card-icon"><Building2 size={21} strokeWidth={1.4} aria-hidden="true" /></span><span className="department-row-summary"><strong title={value.name}>{value.name}</strong><span>{value.employee_count} 位员工</span></span></div>
            <Nav.Link as="button" type="button" eventKey="members" className="employee-row-details" aria-label={`查看${value.name}的部门人员`} onClick={() => openDepartment(value)}>部门人员</Nav.Link>
            <Nav.Link as="button" type="button" eventKey="permissions" className="employee-row-permissions" disabled={!canManagePermissions || !value.id} onClick={() => value.id && setPermissionTarget({ kind: 'department', id: value.id, name: value.name })}>权限</Nav.Link>
          </Nav>)}
          {!departmentLoading && !departmentError && !visibleDepartments.length && <div className="personnel-grid-empty">{departmentQuery ? '没有找到这个部门' : '还没有部门，点击「创建部门」开始。'}</div>}
        </div>
      </> : selected ? <div className="employee-detail-view">
        <div className="personnel-view-heading"><div><button type="button" className="personnel-back" onClick={() => setSelected(null)}><ArrowLeft size={15} />返回部门</button></div><button type="button" className="console-ghost" onClick={() => edit(selected)}><Pencil size={14} />编辑资料</button></div>
        <div className="employee-detail-card"><div className="employee-detail-identity"><span className="employee-detail-avatar">{selected.avatar ? <img src={selected.avatar} alt={selected.name || selected.account} /> : <UserRound size={48} strokeWidth={1.2} />}</span><div><h2 ref={viewHeading} tabIndex={-1}>{selected.name || selected.account}</h2><p>{selected.department || '未分配部门'}{selected.job_title && ` · ${selected.job_title}`}</p><span className={'personnel-status ' + selected.employment_status}>{selected.employment_status === 'active' ? '在职' : '离职'}</span></div></div>
          <dl className="employee-detail-fields">{([['姓名', selected.name], ['账户', selected.account], ['部门', selected.department], ['岗位', selected.job_title], ['手机', selected.phone], ['登录密码', '已设置']] as const).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value || '未填写'}</dd></div>)}<div className="employee-detail-linked"><dt>关联账号</dt><dd>{selected.linked_accounts.length ? selected.linked_accounts.map(value => <span key={value.platform}>{value.platform} · {value.account}</span>) : '未关联'}</dd></div></dl>
        </div>
      </div> : <>
        <div className="personnel-view-heading"><div><button type="button" className="personnel-back" onClick={allDepartments}><ArrowLeft size={14} />全部部门</button><h2 ref={viewHeading} tabIndex={-1}>{department.name}</h2><span>{loading ? '正在读取人员…' : `${total} 位员工`}</span></div><div className="personnel-view-actions">{department.id && <><button type="button" className="console-ghost" onClick={() => editDepartment(department)}><Pencil size={13} />修改部门</button><button type="button" className="console-ghost personnel-delete-button" aria-label="删除部门" onClick={() => { setDeleteError(''); setDeleteTarget(department) }}><Trash2 size={14} /></button></>}<button type="button" className="console-primary" disabled title="新增员工功能待接入"><Plus size={14} />新增员工</button></div></div>
        <div className="personnel-toolbar"><form className="personnel-search" onSubmit={event => { event.preventDefault(); setPage(1); setFilter(query) }}><Search size={15} aria-hidden="true" /><input aria-label="搜索人员" placeholder="搜索姓名、账户或手机" value={query} maxLength={80} onChange={event => setQuery(event.target.value)} /><button type="submit" className="console-ghost">搜索</button></form><select aria-label="在职状态筛选" value={status} onChange={event => { setStatus(event.target.value); setPage(1) }}><option value="">全部状态</option><option value="active">在职</option><option value="resigned">离职</option></select><button type="button" className="console-ghost" disabled={loading} aria-label="刷新人员" onClick={() => setRefresh(value => value + 1)}><RefreshCw size={14} className={loading ? 'spin' : ''} /></button></div>
        {notice && <p className="console-error" role="alert">{notice}</p>}
        <div className="personnel-card-grid employee-grid" aria-label="员工列表" aria-busy={loading}>
          {loading ? <div className="personnel-grid-empty" role="status"><LoaderCircle size={20} className="spin" />正在读取人员</div> : !notice && rows.map(row => <Nav key={row.id} fill className={'employee-card employee-row ' + cardTone(department.name)} aria-label={`${row.name || row.account}的资料与权限`}>
            <div className="employee-row-identity"><span className="employee-card-photo">{row.avatar ? <img src={row.avatar} alt="" loading="lazy" /> : <span className="employee-avatar-placeholder">{(row.name || row.account).slice(0, 2).toLocaleUpperCase()}</span>}</span><span className="employee-card-name" title={row.name || row.account}>{row.name || row.account}</span></div>
            <Nav.Link as="button" type="button" eventKey="details" className="employee-row-details" aria-label={`查看${row.name || row.account}的详细资料`} onClick={() => setSelected(row)}>详细资料</Nav.Link>
            <Nav.Link as="button" type="button" eventKey="permissions" className="employee-row-permissions" disabled={!canManagePermissions} onClick={() => setPermissionTarget({ kind: 'user', id: row.id, name: row.name || row.account })}>权限</Nav.Link>
          </Nav>)}
          {!loading && !notice && !rows.length && <div className="personnel-grid-empty">{filter || status ? '没有符合条件的员工' : '这个部门暂时没有员工'}</div>}
        </div>
        {!notice && total > 50 && <div className="personnel-pagination"><span>共 {total} 人</span><div><button type="button" className="console-ghost" disabled={page <= 1 || loading} aria-label="上一页" onClick={() => setPage(value => value - 1)}><ChevronLeft size={14} /></button><span>{page} / {Math.max(1, Math.ceil(total / 50))}</span><button type="button" className="console-ghost" disabled={page * 50 >= total || loading} aria-label="下一页" onClick={() => setPage(value => value + 1)}><ChevronRight size={14} /></button></div></div>}
      </>}
    </div></div>
    <dialog ref={deleteDialog} className="personnel-dialog department-dialog" aria-labelledby="department-delete-title" onCancel={event => { event.preventDefault(); if (!deleting) setDeleteTarget(null) }}>
      {deleteTarget && <form onSubmit={deleteDepartment}><div className="console-modal-heading"><h2 id="department-delete-title">删除部门</h2><button type="button" className="chat-header-action" aria-label="取消删除部门" disabled={deleting} onClick={() => setDeleteTarget(null)}><X size={18} /></button></div><p className="department-delete-description">确定删除「{deleteTarget.name}」吗？该部门员工将移至“未分配部门”，个人资料与账号会保留。</p><p className="console-form-error" role="alert">{deleteError}</p><div className="console-modal-footer"><button type="button" className="console-ghost" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button><button type="submit" className="console-primary department-delete-confirm" disabled={deleting}>{deleting ? <LoaderCircle size={14} className="spin" /> : <Trash2 size={14} />}确认删除</button></div></form>}
    </dialog>
    <dialog ref={departmentDialog} className="personnel-dialog department-dialog" aria-labelledby="department-form-title" onCancel={event => { event.preventDefault(); if (!departmentSaving) setDepartmentForm(null) }}>
      {departmentForm && <form onSubmit={saveDepartment}><div className="console-modal-heading"><h2 id="department-form-title">{departmentForm.id ? '修改部门' : '创建部门'}</h2><button type="button" className="chat-header-action" aria-label="关闭部门编辑" disabled={departmentSaving} onClick={() => setDepartmentForm(null)}><X size={18} /></button></div><label className="console-field"><span>部门名称</span><input autoFocus required maxLength={100} value={departmentForm.name} disabled={departmentSaving} onChange={event => setDepartmentForm({ ...departmentForm, name: event.target.value })} /></label><p className="console-form-error" role="alert">{departmentFormError}</p><div className="console-modal-footer"><button type="button" className="console-ghost" disabled={departmentSaving} onClick={() => setDepartmentForm(null)}>取消</button><button type="submit" className="console-primary" disabled={departmentSaving}>{departmentSaving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存</button></div></form>}
    </dialog>
    <dialog ref={dialog} className="personnel-dialog" aria-labelledby="employee-form-title" onCancel={event=>{event.preventDefault();if(!saving)setForm(null)}}>
      {form&&<form onSubmit={save}><div className="console-modal-heading"><h2 id="employee-form-title">编辑人员</h2><button type="button" className="chat-header-action" aria-label="关闭人员编辑" disabled={saving} onClick={()=>setForm(null)}><X size={18}/></button></div>
        <div className="personnel-avatar-editor"><span className="personnel-avatar large">{form.avatar?<img src={form.avatar} alt="员工头像"/>:<UserRound size={26}/>}</span><input ref={avatarInput} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={event=>{void avatar(event.target.files?.[0])}}/><button type="button" className="console-ghost" disabled={saving} onClick={()=>avatarInput.current?.click()}><Upload size={13}/>选择头像</button>{form.avatar&&<button type="button" className="console-ghost" disabled={saving} onClick={()=>setForm({...form,avatar:null})}>移除</button>}</div>
        <div className="console-form-grid">
          <label className="console-field"><span>姓名</span><input required maxLength={80} disabled={saving} value={form.name} onChange={event=>setForm({...form,name:event.target.value})}/></label>
          <label className="console-field"><span>账户</span><input required maxLength={160} disabled={saving} autoComplete="off" value={form.account} onChange={event=>setForm({...form,account:event.target.value})}/></label>
          <label className="console-field"><span>部门</span><select disabled={saving} value={form.department} onChange={event=>setForm({...form,department:event.target.value})}><option value="">未分配部门</option>{departments.filter(value=>value.id).map(value=><option key={value.id} value={value.name}>{value.name}</option>)}</select></label>
          <label className="console-field"><span>岗位</span><input maxLength={100} disabled={saving} value={form.job_title} onChange={event=>setForm({...form,job_title:event.target.value})}/></label>
          <label className="console-field"><span>手机</span><input type="tel" maxLength={32} disabled={saving} value={form.phone} onChange={event=>setForm({...form,phone:event.target.value})}/></label>
          <label className="console-field"><span>在职状态</span><select disabled={saving} value={form.employment_status} onChange={event=>setForm({...form,employment_status:event.target.value as 'active'|'resigned'})}><option value="active">在职</option><option value="resigned">离职</option></select></label>
        </div>
        <label className="console-field"><span>密码（留空保持原密码）</span><input type="password" minLength={8} maxLength={256} autoComplete="new-password" disabled={saving} value={form.password} onChange={event=>setForm({...form,password:event.target.value})}/></label>
        <div className="personnel-associated"><span>关联账号</span><p>{form.linked_accounts.length?form.linked_accounts.map(value=>`${value.platform} · ${value.account}`).join('；'):'未关联'}</p><small>员工在控制台完成有赞或飞书授权后自动关联。</small></div>
        <p className="console-form-error" role="alert">{error}</p>
        <div className="console-modal-footer"><button type="button" className="console-ghost" disabled={saving} onClick={()=>setForm(null)}>取消</button><button type="submit" className="console-primary" disabled={saving}>{saving?<LoaderCircle size={14} className="spin"/>:<Save size={14}/>}保存</button></div>
      </form>}
    </dialog>  </section>
}
