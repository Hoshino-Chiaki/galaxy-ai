import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Check, Download, FileText, LoaderCircle, Paperclip, Plus, Save, Trash2, X } from 'lucide-react'
import { service } from '../service'
import './personal-prompt.css'

type Attachment = { id: string; filename: string; size: number }
type Settings = { prompt: string; files: Attachment[]; used_bytes: number; reserved_bytes: number; quota_bytes: number; available_bytes: number }
function size(bytes: number) { return bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 ** 2).toFixed(2)} MB` }

export default function PersonalPrompt() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [prompt, setPrompt] = useState(''), [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [confirm, setConfirm] = useState('')
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const alive = useRef(true), uploading = useRef(false)
  async function load(initialize = false) {
    const value = await service<Settings>('prompt.get')
    if (!alive.current) return
    setSettings(value)
    if (initialize) { setPrompt(value.prompt); setSaved(value.prompt) }
  }
  useEffect(() => {
    alive.current = true
    void load(true).catch(reason => { if (alive.current) setError(reason.message) })
    const unsubscribe = window.desktop?.service.onUploadProgress(value => { if (uploading.current) setProgress(value) })
    return () => { alive.current = false; unsubscribe?.() }
  }, [])
  async function save(event: FormEvent) {
    event.preventDefault(); if (busy) return
    setBusy('save'); setError(''); setNotice('')
    try {
      const value = await service<{ prompt: string }>('prompt.save', { prompt })
      if (alive.current) { setSaved(value.prompt); setNotice('已保存，下次提问时生效。') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy('') }
  }
  async function upload() {
    if (busy) return
    uploading.current = true; setBusy('upload'); setProgress(null); setError(''); setNotice('')
    try {
      const result = await service<{ files: Attachment[]; failures: { filename: string; message: string }[] }>('prompt.upload')
      if (!alive.current) return
      if (result.files.length) setNotice(`已添加 ${result.files.length} 个附件，下次提问时生效。`)
      if (result.failures.length) setError(result.failures.map(item => `${item.filename}：${item.message}`).join('\n'))
      await load()
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { uploading.current = false; if (alive.current) { setBusy(''); setProgress(null) } }
  }
  async function fileAction(action: 'download' | 'delete', file: Attachment) {
    if (busy) return
    setBusy(file.id); setError(''); setNotice('')
    try {
      await service(`prompt.${action}`, { id: file.id })
      if (!alive.current) return
      if (action === 'delete') { setConfirm(''); await load(); setNotice('附件已删除。') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setBusy('') }
  }
  const percent = settings ? Math.min(100, (settings.used_bytes + settings.reserved_bytes) / settings.quota_bytes * 100) : 0
  return <div id="ai-personal-panel" role="tabpanel" aria-labelledby="ai-tab-personal" className="personal-prompt">
    <form className="console-section prompt-section" onSubmit={save}>
      <div className="console-section-heading"><h2><FileText size={16} />我的提示词</h2><span>仅对本人 AI 生效</span></div>
      <p className="console-section-description">告诉 AI 你的工作习惯、回复格式与长期要求。每次提问重新读取最新设置。</p>
      <label className="sr-only" htmlFor="personal-prompt-text">个人提示词</label>
      <textarea id="personal-prompt-text" rows={9} maxLength={20000} disabled={!settings || !!busy} value={prompt} onChange={event => { setPrompt(event.target.value); setNotice('') }} placeholder="例如：请用中文回答，先给结论，再解释依据。金额使用人民币并注明统计口径。" />
      <div className="prompt-footer"><span>{prompt.length.toLocaleString()} / 20,000</span><button type="submit" className="console-primary" disabled={!settings || !!busy || prompt === saved}>{busy === 'save' ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存提示词</button></div>
    </form>
    <section className="console-section personal-prompt-files" aria-label="提示词附件">
      <div className="console-section-heading"><h2><Paperclip size={16} />提示词附件</h2><button type="button" className="console-ghost" disabled={!settings || !!busy || settings.available_bytes === 0} onClick={() => { void upload() }}>{busy === 'upload' ? <LoaderCircle size={14} className="spin" /> : <Plus size={14} />}添加附件</button></div>
      <p className="console-section-description">AI 回答前先读取这些附件。内容仅向本人的 AI 开放；无法解析的文件会如实说明。</p>
      <div className="personal-prompt-capacity"><span>已用 {size(settings?.used_bytes || 0)}</span><span>合计最多 25 MB</span><div role="progressbar" aria-label="提示词附件已用空间" aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${percent}%` }} /></div>{!!settings?.reserved_bytes && <small>上传中已预留 {size(settings.reserved_bytes)}</small>}</div>
      {progress && <div className="personal-prompt-upload" role="status"><span>{progress.filename}</span><progress max={100} value={progress.percent} /><span>{progress.percent}%</span><button type="button" className="console-ghost" aria-label="取消附件上传" onClick={() => { void service('prompt.cancel', { id: progress.upload_request_id }).catch(() => {}) }}><X size={13} /></button></div>}
      {!settings && !error ? <p className="console-section-description"><LoaderCircle size={14} className="spin" /> 正在读取</p> : settings?.files.length ? <ul className="personal-prompt-file-list">{settings.files.map(file => <li key={file.id}><span className="personal-prompt-file-icon"><FileText size={19} /></span><div><strong title={file.filename}>{file.filename}</strong><small>{size(file.size)}</small></div>{confirm === file.id ? <><button type="button" className="console-ghost prompt-delete" disabled={!!busy} onClick={() => { void fileAction('delete', file) }}>确认删除</button><button type="button" className="console-ghost" disabled={!!busy} onClick={() => setConfirm('')}>取消</button></> : <><button type="button" className="console-ghost" aria-label={`下载 ${file.filename}`} disabled={!!busy} onClick={() => { void fileAction('download', file) }}><Download size={14} /></button><button type="button" className="console-ghost" aria-label={`删除 ${file.filename}`} disabled={!!busy} onClick={() => setConfirm(file.id)}><Trash2 size={14} /></button></>}</li>)}</ul> : <div className="personal-prompt-empty"><Paperclip size={24} strokeWidth={1.4} /><span>添加工作规范、写作要求等长期参考资料</span></div>}
    </section>
    {error && <p className="console-error personal-prompt-feedback" role="alert">{error}</p>}
    {notice && <p className="console-success personal-prompt-feedback" role="status"><Check size={14} />{notice}</p>}
  </div>
}
