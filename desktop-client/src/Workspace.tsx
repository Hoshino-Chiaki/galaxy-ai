import { useEffect, useRef, useState } from 'react'
import { CalendarClock, Building2, ChevronDown, Cpu, Info, Users, Link2, SlidersHorizontal, Sparkles, FolderOpen, HardDrive, Download } from 'lucide-react'
import AiWorkspace from './AiWorkspace'
import AdminConsole from './AdminConsole'
import YouzanSettingsPanel from './YouzanSettings'
import GuanjiaSettings from './GuanjiaSettings'
import FeishuSettings from './FeishuSettings'
import ClawbotSettings from './ClawbotSettings'
import AboutGalaxy from './components/AboutGalaxy'
import PersonnelManagement from './components/PersonnelManagement'
import AiUsage from './components/AiUsage'
import FileManager from './components/FileManager'
import TaskSchedule, { type ScheduleIntent } from './components/TaskSchedule'
import UpdateDialog from './components/UpdateDialog'
import PersonalCenter from './components/PersonalCenter'
import './console.css'
import './workspace.css'
import './workspace-shell.css'

type Props = { user: CurrentUser; onLogout: () => Promise<void>; signingOut: boolean; error: string; onProfileChange: (profile: PersonalProfile) => void }

export default function Workspace({ user, onLogout, signingOut, error, onProfileChange }: Props) {
  const [module, setModule] = useState<'ai' | 'ai-settings' | 'console-youzan' | 'console-feishu' | 'console-guanjia' | 'console-clawbot' | 'pool-feishu' | 'erp-personnel' | 'erp-ai-usage' | 'schedule' | 'about' | 'files-library' | 'files-published'>('ai')
  const [scheduleIntent, setScheduleIntent] = useState<ScheduleIntent | null>(null)
  function scheduleChat(intent: ScheduleIntent) { setScheduleIntent(intent); setModule('ai') }
  const [consoleOpen, setConsoleOpen] = useState(false)
  const [poolOpen, setPoolOpen] = useState(false)
  const [erpOpen, setErpOpen] = useState(false)
  const [filesOpen, setFilesOpen] = useState(false)
  const canConfigure = user.permissions.includes('ai:configure')
  const canAI = user.permissions.includes('ai:chat')
  const canManagePeople = user.permissions.includes('users:manage')
  const canUsage = user.permissions.includes('ai:usage:read')
  const canERP = canManagePeople || canUsage
  const canFiles = user.permissions.includes('file:read')
  const hasChannel = (channel: string) => user.permissions.some(code => code.startsWith(channel + ':') && (channel === 'guanjia' || channel === 'clawbot' || code !== channel + ':configure'))
  const [updates, setUpdates] = useState<UpdateStatus | null>(null)
  const [updateOpen, setUpdateOpen] = useState(false)
  const promptedVersion = useRef('')
  useEffect(() => {
    let valid = true
    const api = window.desktop?.updates
    if (!api) return
    const accept = (status: UpdateStatus) => {
      if (!valid) return
      setUpdates(status)
      if (status.phase === 'available' && status.release?.version !== promptedVersion.current) {
        promptedVersion.current = status.release!.version
        setUpdateOpen(true)
      }
    }
    const unsubscribe = api.onStatus(accept)
    void api.state().then(accept).catch(() => {})
    return () => { valid = false; unsubscribe() }
  }, [])
  async function checkUpdate() {
    const state = await window.desktop?.updates.check()
    if (state) { setUpdates(state); if (state.phase === 'available') setUpdateOpen(true) }
  }
  async function installUpdate() {
    setUpdateOpen(true)
    const state = await window.desktop?.updates.install()
    if (state) setUpdates(state)
  }
  return <main className="workspace-page" aria-label="工作空间">
    <aside className="workspace-sidebar" aria-label="模块导航">
      <div className="workspace-brand"><span><Sparkles size={19} strokeWidth={1.5} /></span><strong>Galaxy AI</strong></div>

      <div className="module-caption">应用</div>
      <nav className="workspace-nav" aria-label="功能导航">
        {canAI && <button type="button" className={`nav-item${module === 'ai' ? ' active' : ''}`} aria-current={module === 'ai' ? 'page' : undefined}
          aria-label="AI" title="AI" onClick={() => setModule('ai')}>
          <span className="nav-icon ai-nav-avatar"><img src="./ai.png" alt="" /></span><span className="nav-label">AI</span>{module === 'ai' && <span className="active-indicator" />}
        </button>}
        {user.permissions.includes('schedule:read') && <button type="button" className={`nav-item${module === 'schedule' ? ' active' : ''}`} aria-label="任务调度" aria-current={module === 'schedule' ? 'page' : undefined} onClick={() => setModule('schedule')}><span className="nav-icon"><CalendarClock size={16} /></span><span className="nav-label">任务调度</span><small className="schedule-experiment">实验</small></button>}
        {canConfigure && <button type="button" className={`nav-item${module === 'ai-settings' ? ' active' : ''}`} onClick={() => setModule('ai-settings')} aria-label="AI 设置">
          <span className="nav-icon"><Cpu size={16} /></span><span className="nav-label">AI 设置</span></button>}
        {(hasChannel('youzan') || hasChannel('feishu') || hasChannel('guanjia') || hasChannel('clawbot')) && <>
          <button type="button" className={`nav-item console-nav${module.startsWith('console-') ? ' console-active' : ''}`} aria-label="我的账号" title="我的账号" aria-expanded={consoleOpen}
            onClick={() => { setConsoleOpen(value => !value); if (!consoleOpen) setModule(hasChannel('youzan') ? 'console-youzan' : hasChannel('feishu') ? 'console-feishu' : hasChannel('guanjia') ? 'console-guanjia' : 'console-clawbot') }}>
            <span className="nav-icon"><SlidersHorizontal size={16} strokeWidth={1.6} /></span><span className="nav-label">我的账号</span><ChevronDown size={12} className={consoleOpen ? 'chevron open' : 'chevron'} />
          </button>
          {consoleOpen && <div className="console-subnav">
            {hasChannel('youzan') && <button type="button" className={module === 'console-youzan' ? 'selected' : ''} onClick={() => setModule('console-youzan')} aria-label="我的账号 有赞"><Link2 size={13} /><span className="subnav-label">有赞</span></button>}
            {hasChannel('feishu') && <button type="button" className={module === 'console-feishu' ? 'selected' : ''} onClick={() => setModule('console-feishu')} aria-label="我的账号 飞书"><Link2 size={13} /><span className="subnav-label">飞书</span></button>}
            {hasChannel('guanjia') && <button type="button" className={module === 'console-guanjia' ? 'selected' : ''} onClick={() => setModule('console-guanjia')} aria-label="我的账号 管家婆"><Link2 size={13} /><span className="subnav-label">管家婆</span></button>}
            {hasChannel('clawbot') && <button type="button" className={module === 'console-clawbot' ? 'selected' : ''} onClick={() => setModule('console-clawbot')} aria-label="我的账号 ClawBot"><Link2 size={13} /><span className="subnav-label">ClawBot</span></button>}
          </div>}
        </>}
        {user.permissions.includes('feishu:app:configure') && <>
          <button type="button" className={`nav-item console-nav${module.startsWith('pool-') ? ' console-active' : ''}`} aria-label="账号池" title="账号池" aria-expanded={poolOpen} aria-controls="account-pool-subnav" onClick={() => { setPoolOpen(value => !value); if (!poolOpen) setModule('pool-feishu') }}>
            <span className="nav-icon"><Building2 size={16} strokeWidth={1.6} /></span><span className="nav-label">账号池</span><ChevronDown size={12} className={poolOpen ? 'chevron open' : 'chevron'} />
          </button>
          {poolOpen && <div id="account-pool-subnav" className="console-subnav"><button type="button" className={module === 'pool-feishu' ? 'selected' : ''} aria-label="账号池 飞书设置" onClick={() => setModule('pool-feishu')}><Link2 size={13} /><span className="subnav-label">飞书设置</span></button></div>}
        </>}
        {canERP && <button type="button" className={`nav-item console-nav${module.startsWith('erp-') ? ' console-active' : ''}`} aria-expanded={erpOpen} aria-controls="erp-subnav" aria-label="ERP"
          onClick={() => { setErpOpen(value => !value); if (!erpOpen) setModule(canManagePeople ? 'erp-personnel' : 'erp-ai-usage') }}>
          <span className="nav-icon"><Building2 size={16} strokeWidth={1.6} aria-hidden="true" /></span><span className="nav-label">ERP</span><ChevronDown size={12} className={erpOpen ? 'chevron open' : 'chevron'} aria-hidden="true" />
        </button>}
        {canERP && erpOpen && <div id="erp-subnav" className="console-subnav">{canManagePeople && <button type="button" className={module === 'erp-personnel' ? 'selected' : ''} aria-current={module === 'erp-personnel' ? 'page' : undefined} aria-label="ERP 人员管理" onClick={() => setModule('erp-personnel')}><Users size={13} aria-hidden="true" /><span className="subnav-label">人员管理</span></button>}{canUsage && <button type="button" className={module === 'erp-ai-usage' ? 'selected' : ''} aria-current={module === 'erp-ai-usage' ? 'page' : undefined} aria-label="ERP AI 用量" onClick={() => setModule('erp-ai-usage')}><Cpu size={13} /><span className="subnav-label">AI 用量</span></button>}</div>}
        {canFiles && <><button type="button" className={`nav-item console-nav${module.startsWith('files-') ? ' console-active' : ''}`} aria-label="文件管理" aria-expanded={filesOpen} onClick={() => { setFilesOpen(v => !v); if (!filesOpen) setModule('files-library') }}><span className="nav-icon"><FolderOpen size={16} /></span><span className="nav-label">文件管理</span><ChevronDown size={12} className={filesOpen ? 'chevron open' : 'chevron'} /></button>{filesOpen && <div className="console-subnav file-subnav"><button type="button" className={module === 'files-library' ? 'selected' : ''} aria-label="文件管理 AI 文件存储" title="AI 文件存储" onClick={() => setModule('files-library')}><HardDrive size={13} /><span className="subnav-label">AI 文件存储</span></button><button type="button" className={module === 'files-published' ? 'selected' : ''} aria-label="文件管理 托管文件" title="托管文件" onClick={() => setModule('files-published')}><Download size={13} /><span className="subnav-label">托管文件</span></button></div>}</>}
        <button type="button" className={`nav-item${module === 'about' ? ' active' : ''}`} aria-current={module === 'about' ? 'page' : undefined} aria-label="关于 Galaxy AI" title="关于 Galaxy AI" onClick={() => setModule('about')}>
          <span className="nav-icon"><Info size={16} aria-hidden="true" /></span><span className="nav-label">关于 Galaxy AI</span></button>
      </nav>

      <div className="sidebar-brand"><span className="brand-symbol"><Sparkles size={14} strokeWidth={1.6} /></span><span>galaxy ai</span></div>
    </aside>
    <div className="workspace-main">
    {canAI && <AiWorkspace key={user.id} user={user} error={error} active={module === 'ai'} scheduleIntent={scheduleIntent} />}
    {module === 'ai' && !canAI && <div className="console-empty"><p>当前账号尚未开通 AI 对话，请选择已开通的功能。</p></div>}
    {module === 'schedule' && user.permissions.includes('schedule:read') && <TaskSchedule user={user} onChat={scheduleChat} />}
    {canManagePeople && module === 'erp-personnel' && <PersonnelManagement user={user} />}
    {canUsage && module === 'erp-ai-usage' && <AiUsage />}
    {canFiles && module.startsWith('files-') && <FileManager key={module} mode={module === 'files-library' ? 'library' : 'published'} user={user} />}
    {module === 'about' && <AboutGalaxy status={updates} onCheck={() => { void checkUpdate() }} onInstall={() => { void installUpdate() }} />}
    {canConfigure && module === 'ai-settings' && <AdminConsole />}
    {module.startsWith('console-') && <section className="console-panel" aria-label="我的账号">
      <div className="console-scroll">{module === 'console-youzan' && hasChannel('youzan') ? <YouzanSettingsPanel /> : module === 'console-clawbot' && hasChannel('clawbot') ? <ClawbotSettings /> : module === 'console-guanjia' && hasChannel('guanjia') ? <GuanjiaSettings /> : module === 'console-feishu' && hasChannel('feishu') ? <FeishuSettings key="personal" user={user} mode="personal" /> : null}</div>
    </section>}
    {module === 'pool-feishu' && user.permissions.includes('feishu:app:configure') && <section className="console-panel" aria-label="账号池飞书设置"><div className="console-scroll"><FeishuSettings key="application" user={user} mode="application" /></div></section>}
    </div>
    <PersonalCenter user={user} onProfileChange={onProfileChange} onLogout={onLogout} signingOut={signingOut} />
    {updateOpen && updates && <UpdateDialog status={updates} onClose={() => setUpdateOpen(false)} onInstall={() => { void installUpdate() }} />}
  </main>
}
