import { useEffect, useState } from 'react'
import { Download, FileText, Image, LoaderCircle, X } from 'lucide-react'
import { service } from '../service'

export default function ChatAttachmentCard({ file, onRemove, disabled = false }: { file: ChatAttachment; onRemove?: () => void; disabled?: boolean }) {
  const [source, setSource] = useState(''), [downloading, setDownloading] = useState(false), [error, setError] = useState('')
  const image = file.media_type.startsWith('image/') && !file.filename.toLowerCase().endsWith('.svg')
  useEffect(() => {
    let valid = true
    if (image) void service<{ image: string }>('storage.thumbnail', { id: file.id }).then(value => { if (valid) setSource(value.image) }).catch(() => {})
    return () => { valid = false }
  }, [file.id, image])
  async function download() {
    if (downloading) return
    setDownloading(true); setError('')
    try { await service('storage.download', { id: file.id }) }
    catch (reason) { setError((reason as Error).message) }
    finally { setDownloading(false) }
  }
  const size = file.size < 1024 * 1024 ? `${Math.max(0.1, file.size / 1024).toFixed(1)} KB` : `${(file.size / 1024 / 1024).toFixed(1)} MB`
  return <div className={'chat-attachment' + (image ? ' image' : '')} title={error || file.filename}>
    <span className="chat-attachment-icon">{source ? <img src={source} alt={file.filename} /> : image ? <Image size={21} /> : <FileText size={21} />}</span>
    <div className="chat-attachment-info"><strong>{file.filename}</strong><small>{error || `${size} · ${onRemove ? '已上传' : '附件'}`}</small></div>
    {onRemove ? <button type="button" disabled={disabled} aria-label={`移除附件 ${file.filename}`} title="从本条消息移除，文件仍保留" onClick={onRemove}><X size={13} /></button>
      : <button type="button" disabled={downloading} aria-label={`下载附件 ${file.filename}`} onClick={() => { void download() }}>{downloading ? <LoaderCircle size={14} className="spin" /> : <Download size={14} />}</button>}
  </div>
}
