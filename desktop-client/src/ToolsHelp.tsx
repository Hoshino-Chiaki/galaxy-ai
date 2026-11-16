import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowRight, ArrowUpRight, Check, Folder, LoaderCircle, RefreshCw, TerminalSquare, X } from 'lucide-react'
import { service } from './service'
import { toolCategoryLabel } from './toolCategories'

type Tool = { tool: string; category: string | null; description: string }
type Catalog = { version?: string; data: Tool[] }
const group = (tool: Tool) => tool.category ?? 'general'
const label = (tool: Tool) => tool.category && tool.tool.startsWith(tool.category + '.') ? tool.tool.slice(tool.category.length + 1) : tool.tool

export default function ToolsHelp() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [category, setCategory] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const active = useRef(false)
  const categories = useMemo(() => [...new Set(catalog?.data.map(group) ?? [])], [catalog])
  const tools = catalog?.data.filter(tool => group(tool) === category) ?? []
  const detail = tools.find(tool => tool.tool === selected)

  async function load() {
    setLoading(true); setError('')
    try {
      const value = await service<Catalog>('admin.tools')
      if (!Array.isArray(value.data) || value.data.some(tool => typeof tool.tool !== 'string' || typeof tool.description !== 'string' || tool.category !== null && typeof tool.category !== 'string')) throw new Error('工具目录格式异常。')
      if (active.current) {
        setCatalog(value)
        const groups = [...new Set(value.data.map(group))]
        setCategory(current => groups.includes(current) ? current : groups[0] ?? '')
        setSelected(current => value.data.some(tool => tool.tool === current) ? current : null)
      }
    } catch (reason) { if (active.current) setError((reason as Error).message) }
    finally { if (active.current) setLoading(false) }
  }

  useEffect(() => { active.current = true; void load(); return () => { active.current = false } }, [])
  function choose(value: string) { setCategory(value); setSelected(null) }
  function close() { if (selected) document.getElementById('tool-card-' + selected)?.focus(); setSelected(null) }

  return <section id="ai-tools-panel" className="console-section tools-library" role="tabpanel" aria-labelledby="ai-tab-tools">
    {error && <div className="tools-error"><p className="console-error tool-help-error" role="status">{error}</p>{!catalog && <button type="button" className="console-ghost" disabled={loading} onClick={() => { void load() }}>重试</button>}</div>}
    {!catalog && loading ? <div className="tools-loading" role="status"><LoaderCircle size={20} className="spin" /><span>正在加载工具</span></div> : catalog && <div className="tools-layout">
      <aside className="tools-sidebar" aria-label="工具目录"><div className="tools-sidebar-heading"><h2>工具目录</h2><span>{catalog.data.length} 个工具 · {categories.length} 个分类</span></div>
      <div className="tools-categories" role="tablist" aria-label="工具分类" aria-orientation="vertical" onKeyDown={event => {
        if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || !categories.length) return
        event.preventDefault()
        const index = categories.indexOf(category)
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? categories.length - 1 : (index + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : categories.length - 1)) % categories.length
        choose(categories[next]); document.getElementById('tool-category-' + categories[next])?.focus()
      }}>
        {categories.map(value => <button key={value} id={'tool-category-' + value} type="button" role="tab" aria-selected={category === value} aria-controls="tools-category-panel" tabIndex={category === value ? 0 : -1} onClick={() => choose(value)}>
          <Folder size={14} aria-hidden="true" /><span>{toolCategoryLabel(value)}</span><small aria-hidden="true">{catalog.data.filter(tool => group(tool) === value).length}</small></button>)}
      </div></aside>
      <div className="tools-content"><header className="tools-toolbar"><div><Folder size={16} strokeWidth={1.5} /><h2>{toolCategoryLabel(category)}</h2><span>{tools.length} 个工具</span></div><button type="button" className="console-ghost" disabled={loading} onClick={() => { void load() }}>{loading ? <LoaderCircle size={13} className="spin" /> : <RefreshCw size={13} />}刷新</button></header>
      {tools.length ? <div id="tools-category-panel" className="tools-grid" role="tabpanel" aria-labelledby={'tool-category-' + category}>
        {tools.map(tool => <button id={'tool-card-' + tool.tool} type="button" key={tool.tool} className={`tool-card${selected === tool.tool ? ' selected' : ''}`} aria-label={tool.tool + ' 查看描述'} aria-expanded={selected === tool.tool} aria-controls="tool-description-panel" onClick={() => setSelected(current => current === tool.tool ? null : tool.tool)}>
          <span className="tool-card-title"><span className="tool-card-symbol"><TerminalSquare size={16} strokeWidth={1.5} /></span><strong>{label(tool)}</strong><span className="tool-card-arrow">{selected === tool.tool ? <Check size={14} /> : <ArrowUpRight size={14} />}</span></span>
          <span className="tool-card-preview">{tool.description.split('。')[0].split('，调用')[0]}</span>
        </button>)}
      </div> : <div className="console-empty"><TerminalSquare size={24} /><p>当前没有已注册的工具</p></div>}
      {detail ? <section id="tool-description-panel" className="tool-description" aria-label="工具描述">
        <header><div><span className="tool-description-category"><Folder size={11} />{toolCategoryLabel(category)}</span><h3>{label(detail)}</h3></div><button type="button" className="chat-header-action" aria-label="关闭工具描述" onClick={close}><X size={17} /></button></header>
        <p>{detail.description}</p>
      </section> : tools.length > 0 && <div className="tools-hint"><ArrowRight size={13} /><span>选择一个工具，查看用途和参数说明。</span></div>}
      </div>
    </div>}
  </section>
}
