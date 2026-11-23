import { Download, LoaderCircle, RefreshCw, Sparkles } from 'lucide-react'
import ReleaseNotes from './ReleaseNotes'
import './updates.css'

type Props = { status: UpdateStatus | null; onCheck: () => void; onInstall: () => void }
export default function AboutGalaxy({ status, onCheck, onInstall }: Props) {
  const busy = status && ['checking', 'downloading', 'verifying', 'installing'].includes(status.phase)
  const available = status?.release && status.phase === 'available'
  const showNotes = status?.release && (available || (status.phase === 'current' && status.release.version === status.current_version))
  return <section className="console-panel" aria-label="关于 Galaxy AI">
    <div className="console-scroll"><div className="console-body about-galaxy">
      <div className="about-identity"><span className="about-symbol"><Sparkles size={30} strokeWidth={1.3} aria-hidden="true" /></span>
        <div><span className="about-version">版本 {status?.current_version || '读取中'}</span></div>
      </div>
      <p className="about-description">Galaxy AI 是为团队打造的智能业务工作台。通过自然对话连接有赞、飞书等业务平台，帮助员工查询数据、分析经营情况、处理日常任务，并生成可下载的报告。每位成员使用自己的授权，共享团队配置，让协作更简单。</p>
      <div className="about-credits"><div><span>CEO</span><strong>陈宇卓</strong></div><div><span>Developer</span><strong>chiaki</strong></div></div>
      <section className="about-update" aria-labelledby="update-heading">
        <div><h3 id="update-heading">软件更新</h3><p role="status">{status?.phase === 'checking' ? '正在检查新版本…' : status?.phase === 'current' ? '当前已是最新版本。' : status?.phase === 'available' ? `新版本 ${status.release?.version} 已发布。` : status?.phase === 'error' ? status.message : status?.phase === 'downloading' ? `正在下载 ${status.percent}%` : status?.phase === 'verifying' ? '正在校验安装包…' : status?.phase === 'installing' ? '正在安装，即将重新打开。' : '启动时自动检查，也可手动检查更新。'}</p></div>
        <button type="button" className="console-ghost" disabled={!!busy} onClick={onCheck}>{status?.phase === 'checking' ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}检查更新</button>
      </section>
      {showNotes && <div className="about-release"><h3>{available ? '新版本' : '本版本更新内容'} {status.release!.version}</h3><ReleaseNotes notes={status.release!.notes} />{available && <button type="button" className="console-primary" onClick={onInstall}><Download size={14} />下载并安装</button>}</div>}
      <p className="about-footer">为真实业务而构建 · 持续迭代</p>
    </div></div>
  </section>
}
