import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Cpu, Link2, LogOut, SlidersHorizontal, Sparkles, UserRound } from 'lucide-react'
import AiWorkspace from './AiWorkspace'
import AdminConsole from './AdminConsole'
import YouzanSettingsPanel from './YouzanSettings'
import FeishuSettings from './FeishuSettings'
import './console.css'
import './workspace.css'

type Props = { user: CurrentUser; onLogout: () => Promise<void>; signingOut: boolean; error: string }

export default function Workspace({ user, onLogout, signingOut, error }: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [module, setModule] = useState<'ai' | 'ai-settings' | 'console-youzan' | 'console-feishu'>('ai')
  const [consoleOpen, setConsoleOpen] = useState(false)
  const canConfigure = user.permissions.includes('ai:configure')
  const accountMenu = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!menuOpen) return
    const dismiss = (event: PointerEvent) => {
      if (!accountMenu.current?.contains(event.target as Node)) setMenuOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', escape) }
  }, [menuOpen])

  return <main className="workspace-page" aria-label="工作空间">
    <aside className="workspace-sidebar" aria-label="模块导航">
      <div className="account-menu" ref={accountMenu}>
        <button type="button" className="account-button" aria-label="账户菜单" aria-expanded={menuOpen}
          aria-controls="account-popover" onClick={() => setMenuOpen(value => !value)}>
          <span className="user-avatar" aria-label="头像占位"><UserRound size={19} strokeWidth={1.6} /></span>
          <span className="account-label"><strong>{user.username}</strong><span>{user.role === 'admin' ? '管理员' : '员工'}</span></span>
          <ChevronDown size={13} className={menuOpen ? 'chevron open' : 'chevron'} />
        </button>
        {menuOpen && <div id="account-popover" className="account-popover">
          <div className="account-role">我的账户</div>
          <button type="button" disabled={signingOut} onClick={() => { void onLogout() }}>
            <LogOut size={15} />{signingOut ? '正在退出…' : '退出登录'}
          </button>
        </div>}
      </div>

      <div className="module-caption">应用</div>
      <nav className="workspace-nav" aria-label="功能导航">
        <button type="button" className={`nav-item${module === 'ai' ? ' active' : ''}`} aria-current={module === 'ai' ? 'page' : undefined}
          onClick={() => setModule('ai')}>
          <span className="nav-icon ai-nav-avatar"><img src="./ai.png" alt="" /></span><span>AI</span>{module === 'ai' && <span className="active-indicator" />}
        </button>
        {canConfigure && <button type="button" className={`nav-item${module === 'ai-settings' ? ' active' : ''}`} onClick={() => setModule('ai-settings')} aria-label="AI 设置">
          <span className="nav-icon"><Cpu size={16} /></span><span>AI 设置</span></button>}
        {(user.permissions.includes('youzan:configure') || user.permissions.includes('feishu:configure')) && <>
          <button type="button" className={`nav-item console-nav${module.startsWith('console-') ? ' console-active' : ''}`} aria-expanded={consoleOpen}
            onClick={() => { setConsoleOpen(value => !value); if (!consoleOpen) setModule(user.permissions.includes('youzan:configure') ? 'console-youzan' : 'console-feishu') }}>
            <span className="nav-icon"><SlidersHorizontal size={16} strokeWidth={1.6} /></span><span>控制台</span><ChevronDown size={12} className={consoleOpen ? 'chevron open' : 'chevron'} />
          </button>
          {consoleOpen && <div className="console-subnav">
            {user.permissions.includes('youzan:configure') && <button type="button" className={module === 'console-youzan' ? 'selected' : ''} onClick={() => setModule('console-youzan')} aria-label="控制台 有赞"><Link2 size={13} />有赞</button>}
            {user.permissions.includes('feishu:configure') && <button type="button" className={module === 'console-feishu' ? 'selected' : ''} onClick={() => setModule('console-feishu')} aria-label="控制台 飞书"><Link2 size={13} />飞书</button>}
          </div>}
        </>}
      </nav>

      <div className="sidebar-brand"><span className="brand-symbol"><Sparkles size={14} strokeWidth={1.6} /></span><span>galaxy ai</span></div>
    </aside>
    <AiWorkspace key={user.id} user={user} error={error} active={module === 'ai'} />
    {canConfigure && module === 'ai-settings' && <AdminConsole />}
    {module.startsWith('console-') && <section className="console-panel" aria-label="我的连接控制台">
      <header className="chat-header"><div className="chat-breadcrumb"><span>控制台</span><span className="breadcrumb-divider">/</span><span>{module === 'console-youzan' ? '有赞' : '飞书'}</span></div></header>
      <div className="console-scroll">{module === 'console-youzan' && user.permissions.includes('youzan:configure') ? <YouzanSettingsPanel /> : module === 'console-feishu' && user.permissions.includes('feishu:configure') ? <FeishuSettings user={user} /> : null}</div>
    </section>}
  </main>
}
