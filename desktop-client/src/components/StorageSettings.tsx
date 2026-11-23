import { useEffect, useState, type FormEvent } from 'react'
import { HardDrive, LoaderCircle, Save } from 'lucide-react'
import { service } from '../service'
import './file-manager.css'
export default function StorageSettings() {
  const [quota, setQuota] = useState<number | null>(null), [saving, setSaving] = useState(false), [notice, setNotice] = useState('')
  useEffect(() => { let valid = true; void service<{ quota_mb: number }>('admin.storage').then(v => { if (valid) setQuota(v.quota_mb) }).catch(e => { if (valid) setNotice(e.message) }); return () => { valid = false } }, [])
  async function save(event: FormEvent) {
    event.preventDefault(); if (!quota || saving) return
    setSaving(true); setNotice('')
    try { const value = await service<{ quota_mb: number }>('admin.storageSave', { quota_mb: quota }); setQuota(value.quota_mb); setNotice('容量设置已保存，对所有账号的新上传立即生效。') }
    catch (reason) { setNotice((reason as Error).message) }
    finally { setSaving(false) }
  }
  return <form className="console-section" id="ai-storage-panel" role="tabpanel" aria-labelledby="ai-tab-storage" onSubmit={save}>
    <div className="console-section-heading"><h2><HardDrive size={17} />文件存储</h2><span>所有账号通用</span></div>
    <p className="console-section-description">每个账号独立计算 AI 上传文件总容量，默认 512 MB。解析依赖由服务器统一提供，各账号共享。</p>
    {quota !== null && <><label className="console-field storage-settings-input"><span>每人总容量（MB）</span><input type="number" min={1} step={1} required value={quota} disabled={saving} onChange={event => setQuota(Number(event.target.value))} /></label><p className="console-section-description">上传前预留空间，并发上传合计计算。降低额度不会删除已有文件；空间不足时会拒绝继续上传。托管生成文件单独展示。</p><div className="prompt-footer"><span>1 MB = 1,048,576 字节</span><button type="submit" className="console-primary" disabled={saving}>{saving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存容量设置</button></div></>}
    <p className="console-section-description" role="status">{notice}</p>
  </form>
}
