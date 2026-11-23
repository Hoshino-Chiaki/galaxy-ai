import { useEffect, useRef, useState } from 'react'
import { Archive, Download, File, FileImage, FileSpreadsheet, FileText, FolderOpen, HardDrive, LoaderCircle, RefreshCw, Search, Trash2 } from 'lucide-react'
import { service } from '../service'
import './file-manager.css'

type StoredFile = { id?: string; filename: string; size: number; media_type: string; created_at: number; path?: string; scope?: string; name?: string; temporary?: boolean }
type Listing = { files: StoredFile[]; used_bytes?: number; reserved_bytes?: number; quota_bytes?: number; available_bytes?: number }
export function fileSize(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}
function fileKind(item: StoredFile) {
  const suffix = item.filename.split('.').pop()?.toLowerCase() || ''
  if (item.media_type.startsWith('image/') && suffix !== 'svg') return { icon: FileImage, kind: 'image', label: '图片' }
  if (['xlsx', 'xls', 'csv', 'tsv'].includes(suffix)) return { icon: FileSpreadsheet, kind: 'sheet', label: suffix.toUpperCase() }
  if (['pdf', 'docx', 'doc', 'txt', 'md', 'json'].includes(suffix)) return { icon: FileText, kind: 'document', label: suffix.toUpperCase() }
  if (['zip', 'rar', '7z', 'gz'].includes(suffix)) return { icon: Archive, kind: 'archive', label: suffix.toUpperCase() }
  return { icon: File, kind: 'other', label: suffix.toUpperCase().slice(0, 8) || '文件' }
}
function Thumbnail({ item }: { item: StoredFile }) {
  const [source, setSource] = useState('')
  const target = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let valid = true
    if (!target.current || !item.id) return
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(e => e.isIntersecting)) return
      observer.disconnect()
      void service<{ image: string }>('storage.thumbnail', { id: item.id }).then(value => { if (valid) setSource(value.image) }).catch(() => {})
    })
    observer.observe(target.current)
    return () => { valid = false; observer.disconnect() }
  }, [item.id])
  return <div className="file-card-preview image" ref={target}>{source ? <img src={source} alt={item.filename} /> : <FileImage size={38} strokeWidth={1.3} />}</div>
}
export default function FileManager({ mode, user }: { mode: 'library' | 'published'; user: CurrentUser }) {
  const [listing, setListing] = useState<Listing | null>(null), [loading, setLoading] = useState(true)
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [search, setSearch] = useState('')
  const [busy, setBusy] = useState(''), [confirm, setConfirm] = useState('')
  const alive = useRef(true)
  const library = mode === 'library', canUpload = user.permissions.includes('task:upload')
  async function load() {
    setLoading(true)
    try { const value = await service<Listing>(library ? 'storage.list' : 'storage.published'); if (alive.current) { setListing(value); setError('') } }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setLoading(false) }
  }
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false } }, [mode])
  async function download(item: StoredFile) {
    const key = item.id || item.scope + ':' + item.name
    setBusy(key); setError(''); setNotice('')
    try { const value = await service<{ saved?: boolean }>('storage.download', library ? { id: item.id } : { kind: 'published', scope: item.scope, name: item.name }); if (alive.current && value.saved) setNotice(`已下载「${item.filename}」。`) }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy('') }
  }
  async function remove(item: StoredFile) {
    if (!item.id) return
    setBusy(item.id); setError(''); setNotice('')
    try { await service('storage.delete', { id: item.id }); if (alive.current) { setConfirm(''); await load(); setNotice('文件已删除，空间已释放。') } }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy('') }
  }
  const items = (listing?.files || []).filter(item => item.filename.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  const percent = listing?.quota_bytes ? Math.min(100, ((listing.used_bytes || 0) + (listing.reserved_bytes || 0)) * 100 / listing.quota_bytes) : 0
  return <section className="file-manager" aria-label={library ? 'AI 文件存储' : '托管文件'}>
    <header className="file-manager-header"><div><span className="file-eyebrow">我的文件 · {user.name || user.username}</span><h1>{library ? 'AI 文件存储' : '托管文件'}</h1><p>{library ? '在聊天输入框的「＋」上传文件，这里管理本人的资料。' : '下载本人已发布的附件与托管目录中的共享文件。'}</p></div></header>
    {library && listing && <div className="file-capacity"><div className="file-capacity-icon"><HardDrive size={24} /></div><div className="file-capacity-detail"><div><strong>{fileSize(listing.used_bytes || 0)} <span>/ {fileSize(listing.quota_bytes || 0)}</span></strong><span>剩余 {fileSize(listing.available_bytes || 0)}</span></div><div className="file-capacity-track"><i style={{ width: `${percent}%` }} /></div><small>{listing.reserved_bytes ? `上传中已预留 ${fileSize(listing.reserved_bytes)}` : '容量按所有上传文件合计，包含未过期的任务上传文件。'}</small></div></div>}
    <div className="file-manager-toolbar"><label><Search size={15} /><input aria-label="搜索文件" placeholder="搜索文件名称" value={search} onChange={event => setSearch(event.target.value)} /></label><span>{listing?.files.length || 0} 个文件</span><button type="button" className="console-ghost" aria-label="刷新文件列表" disabled={loading} onClick={() => { void load() }}><RefreshCw size={15} className={loading ? 'spin' : ''} /></button></div>
    {error && <p className="file-manager-error" role="alert">{error}</p>}{notice && <p className="file-manager-notice" role="status">{notice}</p>}
    {loading && !listing ? <div className="console-empty"><LoaderCircle className="spin" />正在读取文件</div> : items.length ? <div className="file-card-grid">{items.map(item => {
      const kind = fileKind(item), Icon = kind.icon, key = item.id || item.scope + ':' + item.name
      return <article className={`file-card ${kind.kind}`} key={key}>
        {library && kind.kind === 'image' ? <Thumbnail item={item} /> : <div className={`file-card-preview ${kind.kind}`}><Icon size={36} strokeWidth={1.3} /><span>{kind.label}</span></div>}
        <div className="file-card-content"><h3 title={item.filename}>{item.filename}</h3><p><span>{fileSize(item.size)}</span><span>{new Date(item.created_at * 1000).toLocaleDateString('zh-CN')}</span></p>{!library && <small>{item.scope === 'shared' ? '共享托管' : '本人附件'}</small>}{item.temporary && <small>任务临时上传</small>}</div>
        <div className="file-card-actions">{confirm === key ? <><button type="button" className="file-delete-confirm" disabled={!!busy} onClick={() => { void remove(item) }}>确认删除</button><button type="button" disabled={!!busy} onClick={() => setConfirm('')}>取消</button></> : <><button type="button" disabled={!!busy} onClick={() => { void download(item) }}>{busy === key ? <LoaderCircle size={14} className="spin" /> : <Download size={14} />}下载</button>{library && canUpload && <button type="button" className="file-delete-button" aria-label={`删除 ${item.filename}`} disabled={!!busy} onClick={() => setConfirm(key)}><Trash2 size={14} /></button>}</>}</div>
      </article>
    })}</div> : <div className="file-empty"><FolderOpen size={42} strokeWidth={1.2} /><h3>{search ? '没有找到这个文件' : '这里还没有文件'}</h3><p>{library ? '点击聊天输入框的「＋」，上传文档、表格或图片。' : '发布后的附件将显示在这里。'}</p></div>}
  </section>
}
