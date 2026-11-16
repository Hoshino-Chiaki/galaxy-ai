import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CheckCircle2, Folder, LoaderCircle, Save } from 'lucide-react'
import { service } from './service'
import { toolCategoryLabel } from './toolCategories'

type Category = { category: string; prompt: string; tool_count: number }

export default function ToolPrompts() {
  const [categories, setCategories] = useState<Category[] | null>(null)
  const [category, setCategory] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const alive = useRef(true)
  useEffect(() => {
    let valid = true
    alive.current = true
    void service<{ categories: Category[] }>('admin.toolPrompts').then(value => {
      if (!alive.current || !valid) return
      setCategories(value.categories)
      setCategory(value.categories.find(item => item.category === 'youzan')?.category || value.categories[0]?.category || '')
      setDrafts(Object.fromEntries(value.categories.map(item => [item.category, item.prompt])))
    }).catch(reason => { if (alive.current && valid) setError((reason as Error).message) })
    return () => { valid = false; alive.current = false }
  }, [])
  const current = categories?.find(item => item.category === category)
  const draft = drafts[category] || ''
  async function save(event: FormEvent) {
    event.preventDefault()
    if (!current || saving) return
    setSaving(true); setError(''); setNotice('')
    try {
      const value = await service<{ category: string; prompt: string }>('admin.saveToolPrompt', { category, prompt: draft })
      if (alive.current) {
        setCategories(items => items!.map(item => item.category === value.category ? { ...item, prompt: value.prompt } : item))
        setDrafts(items => ({ ...items, [value.category]: value.prompt }))
        setNotice(value.prompt ? '分类提示词已保存，下次发送消息时生效。' : '已清空，该分类不附加提示词。')
      }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }
  return <section id="ai-tool-prompts-panel" className="console-section tools-library" role="tabpanel" aria-labelledby="ai-tab-tool-prompts">
    {error && <p className="console-error" role="alert">{error}</p>}
    {!categories ? !error && <div className="tools-loading"><LoaderCircle size={20} className="spin" />正在读取分类提示词</div> : <div className="tools-layout">
      <aside className="tools-sidebar" aria-label="提示词分类"><div className="tools-sidebar-heading"><h2>工具分类</h2><span>{categories.length} 个分类</span></div>
        <div className="tools-categories">{categories.map(item => <button type="button" key={item.category} aria-pressed={category === item.category}
          className={category === item.category ? 'selected' : ''} disabled={saving} onClick={() => { setCategory(item.category); setError(''); setNotice('') }}>
          <Folder size={14} /><span>{toolCategoryLabel(item.category)}</span><small>{item.prompt.trim() ? '已配置' : '未设置'}</small>
        </button>)}</div></aside>
      {current && <form className="tools-content category-prompt-editor" onSubmit={save}><header className="tools-toolbar"><div><Folder size={16} /><h2>{toolCategoryLabel(category)}</h2><span>{current.tool_count} 个工具</span></div></header>
        <p className="console-section-description">设置这个分类共用的注意事项。留空时不附加分类提示词。</p>
        <label className="sr-only" htmlFor="category-tool-prompt">分类提示词</label>
        <textarea id="category-tool-prompt" value={draft} maxLength={20000} rows={12} disabled={saving}
          onChange={event => { setDrafts(items => ({ ...items, [category]: event.target.value })); setNotice('') }} placeholder="填写这个分类的注意事项；留空表示没有额外说明。" />
        <div className="prompt-footer"><span>{draft.length.toLocaleString()} / 20,000</span><button type="submit" className="console-primary" disabled={saving || draft === current.prompt}>
          {saving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存</button></div>
        {notice && <p className="console-success" role="status"><CheckCircle2 size={13} />{notice}</p>}
      </form>}
    </div>}
  </section>
}
