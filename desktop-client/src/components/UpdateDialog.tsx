import { useEffect, useRef } from 'react'
import { Download, RefreshCw } from 'lucide-react'
import ReleaseNotes, { formatBytes } from './ReleaseNotes'
import './updates.css'

type Props = { status: UpdateStatus; onClose: () => void; onInstall: () => void }
export default function UpdateDialog({ status, onClose, onInstall }: Props) {
  const dialog = useRef<HTMLDialogElement>(null)
  const busy = ['downloading', 'verifying', 'installing'].includes(status.phase)
  const failed = status.phase === 'error'
  const percent = Math.min(100, Math.max(0, Math.round(status.percent || 0)))
  const label = status.phase === 'verifying' ? '正在校验安装包' : status.phase === 'installing' ? '正在安装更新' : '更新下载进度'
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => { element?.close() } }, [])
  return <dialog ref={dialog} className="update-dialog" aria-label="软件更新"
    onCancel={event => { event.preventDefault(); if (!busy) onClose() }}>
    <div className="update-progress">
      <progress max={100} value={percent} aria-label={label} />
      <div className="update-transfer-details"><span>{formatBytes(status.downloaded || 0)} / {formatBytes(status.release?.size || 0)}</span><strong>{percent}%</strong></div>
      {(status.phase === 'verifying' || status.phase === 'installing') && <p className="update-progress-state" role="status">{label}</p>}
    </div>
    {failed && <p className="update-error" role="alert">{status.message}</p>}
    {status.release?.notes && <div className="update-notes" aria-label="更新说明"><ReleaseNotes notes={status.release.notes} /></div>}
    {!busy && <div className="update-dialog-actions"><button type="button" className="console-ghost" onClick={onClose}>稍后更新</button><button type="button" className="console-primary" onClick={onInstall}>{failed ? <RefreshCw size={14} /> : <Download size={14} />}{failed ? '继续更新' : '下载并安装'}</button></div>}
  </dialog>
}
