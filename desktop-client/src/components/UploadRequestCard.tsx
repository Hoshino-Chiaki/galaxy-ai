import { useEffect, useRef, useState } from 'react'
import { Check, File, LoaderCircle, Trash2, Upload, X } from 'lucide-react'
import { service } from '../service'

type Props = { id: string; purpose: string; disabled: boolean; used: boolean;
  onBusyChange(value: boolean): void; onContinue(file: UploadInvitation): Promise<void> }

export default function UploadRequestCard({ id, purpose, disabled, used, onBusyChange, onContinue }: Props) {
  const [invitation, setInvitation] = useState<UploadInvitation | null>(null)
  const [working, setWorking] = useState(false)
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [error, setError] = useState('')
  const alive = useRef(true)
  const choosing = useRef(false)
  useEffect(() => {
    alive.current = true
    let valid = true
    const refresh = () => service<UploadInvitation>('upload.status', { id }).then(value => {
      if (valid) setInvitation(value)
    }).catch(reason => { if (valid) setError((reason as Error).message) })
    void refresh()
    const timer = window.setInterval(() => { if (!choosing.current) void refresh() }, 30000)
    const unsubscribe = window.desktop?.service.onUploadProgress(value => {
      if (valid && value.upload_request_id === id) setProgress(value)
    })
    return () => { valid = false; alive.current = false; window.clearInterval(timer); unsubscribe?.() }
  }, [id])

  async function choose() {
    if (disabled || choosing.current) return
    choosing.current = true; onBusyChange(true); setWorking(true); setError(''); setProgress(null)
    let receipt: UploadInvitation | undefined
    try {
      const result = await service<{ canceled?: boolean; uploaded?: boolean; receipt?: UploadInvitation }>('file.upload', { id })
      if (result.uploaded && result.receipt) {
        receipt = result.receipt
        if (alive.current) setInvitation(receipt)
      }
    } catch (reason) {
      if (alive.current) setError((reason as Error).message)
    } finally {
      choosing.current = false; onBusyChange(false)
      if (alive.current) setWorking(false)
    }
    if (receipt && alive.current) await onContinue(receipt)
  }

  async function remove() {
    if (disabled || working) return
    setError('')
    try {
      await service('upload.delete', { id })
      if (alive.current && invitation) setInvitation({ ...invitation, status: 'deleted' })
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
  }

  const status = invitation?.status
  const ready = status === 'ready'
  const unavailable = status === 'expired' || status === 'deleted'
  const percent = progress?.percent ?? 0
  return <section className="upload-request-card" aria-label="按需上传文件">
    <div className="upload-request-heading"><span>{ready ? <Check size={17} /> : <Upload size={17} />}</span>
      <strong>{working ? progress ? percent >= 99 ? '正在确认上传结果' : '正在上传文件' : '请选择文件' : ready ? '文件已上传' : unavailable ? status === 'deleted' ? '文件已移除' : '上传入口已过期' : '需要一份文件'}</strong>
      {!working && invitation && !unavailable && <button type="button" className="chat-header-action" disabled={disabled} aria-label={ready ? '删除上传文件' : '取消上传入口'} onClick={() => { void remove() }}><Trash2 size={13} /></button>}
    </div>
    <p className="upload-purpose"><b>文件用途</b>{invitation?.purpose || purpose}</p>
    {ready && invitation && <div className="uploaded-filename"><File size={14} /><span>{invitation.filename}</span><small>{((invitation.size ?? 0) / 1024 / 1024).toFixed(2)} MB</small></div>}
    {working && <div className="upload-progress-area" role="status" aria-live="polite">
      <div className="upload-progress-track" role="progressbar" aria-label="文件上传进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><i style={{ width: `${percent}%` }} /></div>
      <span>{progress ? `${percent}% · ${(progress.sent / 1024 / 1024).toFixed(1)} / ${(progress.total / 1024 / 1024).toFixed(1)} MB` : '在文件选择窗口中选择所需资料'}</span>
    </div>}
    <div className="upload-request-footer"><span>{unavailable ? '需要时请让 AI 重新申请' : ready ? used ? '已交给 AI · 到期自动清理' : '上传成功，准备继续处理' : invitation ? `仅本次需求 · 最多 ${Math.floor(invitation.max_size_bytes / 1024 / 1024)} MB · 保留 ${invitation.retention_hours} 小时` : '正在确认上传要求'}</span>
      {working ? <button type="button" className="upload-card-button secondary" onClick={() => { void service('file.upload.cancel', { id }).catch(() => {}) }}><X size={13} />取消上传</button>
        : !unavailable && (ready ? !used && invitation && <button type="button" className="upload-card-button" disabled={disabled} onClick={() => { void onContinue(invitation) }}>继续处理</button>
          : <button type="button" className="upload-card-button" disabled={disabled || status !== 'pending'} onClick={() => { void choose() }}>{invitation ? <Upload size={13} /> : <LoaderCircle className="spin" size={13} />}选择文件</button>)}
    </div>
    {error && <p className="upload-card-error" role="alert">{error}</p>}
  </section>
}
