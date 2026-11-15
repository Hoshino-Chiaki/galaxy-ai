import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Check, CheckCircle2, Cpu, Eye, EyeOff, KeyRound, LoaderCircle, Pencil, Play, Plus, Save, Star, TerminalSquare, Trash2, X } from 'lucide-react'
import ToolsHelp from './ToolsHelp'
import ToolPrompts from './ToolPrompts'
import { service } from './service'
import './console.css'

type ProviderForm = { id?: string; name: string; base_url: string; api_key: string; model: string; max_tokens: number | null; enabled: boolean }
const blankProvider = (): ProviderForm => ({ name: '', base_url: '', api_key: '', model: '', max_tokens: null, enabled: true })

export default function AdminConsole() {
  const [config, setConfig] = useState<AiSettings | null>(null)
  const [aiTab, setAiTab] = useState<'models' | 'prompt' | 'tools' | 'tool-prompts'>('models')
  const [prompt, setPrompt] = useState('')
  const [form, setForm] = useState<ProviderForm | null>(null)
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savingPrompt, setSavingPrompt] = useState(false)
  const [testing, setTesting] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({})
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [success, setSuccess] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const alive = useRef(true)
  const nameInput = useRef<HTMLInputElement>(null)

  async function load(initialize = false) {
    const value = await service<AiSettings>('admin.settings')
    if (!alive.current) return
    setConfig(value)
    if (initialize) setPrompt(value.global_prompt)
  }

  useEffect(() => {
    alive.current = true
    void load(true).catch(() => { if (alive.current) setError('无法读取 AI 配置，请重新进入控制台。') })
    return () => { alive.current = false }
  }, [])

  useEffect(() => {
    if (!form) return
    nameInput.current?.focus()
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !saving) setForm(null) }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [form?.id, !!form, saving])

  function edit(provider?: AiProvider) {
    setForm(provider ? { id: provider.id, name: provider.name, base_url: provider.base_url, model: provider.model,
      max_tokens: provider.max_tokens, enabled: provider.enabled, api_key: '' } : blankProvider())
    setShowKey(false); setFormError(''); setSuccess('')
  }

  async function saveProvider(event: FormEvent) {
    event.preventDefault()
    if (!form || saving) return
    if (!form.name.trim() || !form.base_url.trim() || !form.model.trim() || (!form.id && !form.api_key.trim())) {
      setFormError('请填写名称、Base URL、模型和 API Key。'); return
    }
    setSaving(true); setFormError('')
    try {
      const { id, ...provider } = form
      await service(id ? 'admin.updateProvider' : 'admin.addProvider', id ? { id, provider } : provider)
      if (!alive.current) return
      if (id) setTestResults(value => { const next = { ...value }; delete next[id]; return next })
      setForm(null); setSuccess('AI 配置已保存。')
      await load()
    } catch (reason) { if (alive.current) setFormError((reason as Error).message) }
    finally { if (alive.current) setSaving(false) }
  }

  async function testProvider(id: string) {
    if (testing) return
    setTesting(id)
    setTestResults(value => { const next = { ...value }; delete next[id]; return next })
    try {
      const result = await service<{ latency_ms: number }>('admin.testProvider', { id })
      if (alive.current) setTestResults(value => ({ ...value, [id]: { success: true, message: `连接成功 · ${result.latency_ms} ms` } }))
    } catch (reason) {
      if (alive.current) setTestResults(value => ({ ...value, [id]: { success: false, message: (reason as Error).message } }))
    } finally { if (alive.current) setTesting(null) }
  }

  async function makeDefault(id: string) {
    setError(''); setSuccess('')
    try { await service('admin.defaultProvider', { id }); await load(); if (alive.current) setSuccess('默认 AI 已更新。') }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
  }

  async function removeProvider(id: string) {
    setDeleting(id); setError('')
    try { await service('admin.deleteProvider', { id }); await load(); if (alive.current) setConfirmDelete(null) }
    catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setDeleting(null) }
  }

  async function savePrompt(event: FormEvent) {
    event.preventDefault(); setSavingPrompt(true); setError(''); setSuccess('')
    try {
      await service('admin.prompt', { prompt })
      if (alive.current) { setConfig(value => value ? { ...value, global_prompt: prompt } : value); setSuccess('全局提示词已保存，下次发送消息时生效。') }
    } catch (reason) { if (alive.current) setError((reason as Error).message) }
    finally { if (alive.current) setSavingPrompt(false) }
  }

  return <section className="console-panel" aria-label="管理员控制台">
    <header className="chat-header"><div className="chat-breadcrumb"><span>AI 设置</span></div></header>
    <div className="console-scroll">
      <div className="console-body">
        <div className="console-page-heading"><div><h1>AI</h1><p>{aiTab === 'models' ? '添加并管理可用的模型连接。' : aiTab === 'prompt' ? '统一所有成员的 AI 回复规则。' : aiTab === 'tool-prompts' ? '分别设置每类工具的共用注意事项。' : '按分类浏览工具与调用说明。'}</p></div>{aiTab === 'models' && <button className="console-primary" type="button" onClick={() => edit()}><Plus size={15} />添加 AI</button>}</div>
        <div className="console-tabs" role="tablist" aria-label="AI 配置分类" onKeyDown={event => {
          if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
          event.preventDefault()
          const tabs = ['models', 'prompt', 'tools', 'tool-prompts'] as const
          const next = tabs[(tabs.indexOf(aiTab) + (event.key === 'ArrowLeft' ? tabs.length - 1 : 1)) % tabs.length]
          setAiTab(next); setSuccess(''); setError('')
          document.getElementById(`ai-tab-${next}`)?.focus()
        }}>
          <button id="ai-tab-models" type="button" role="tab" aria-selected={aiTab === 'models'} aria-controls="ai-model-panel" tabIndex={aiTab === 'models' ? 0 : -1}
            onClick={() => { setAiTab('models'); setSuccess(''); setError('') }}><Cpu size={14} />AI 模型</button>
          <button id="ai-tab-prompt" type="button" role="tab" aria-selected={aiTab === 'prompt'} aria-controls="ai-prompt-panel" tabIndex={aiTab === 'prompt' ? 0 : -1}
            onClick={() => { setAiTab('prompt'); setSuccess(''); setError('') }}><Pencil size={13} />全局提示词</button>
          <button id="ai-tab-tools" type="button" role="tab" aria-selected={aiTab === 'tools'} aria-controls="ai-tools-panel" tabIndex={aiTab === 'tools' ? 0 : -1}
            onClick={() => { setAiTab('tools'); setSuccess(''); setError('') }}><TerminalSquare size={14} />工具列表</button>
          <button id="ai-tab-tool-prompts" type="button" role="tab" aria-selected={aiTab === 'tool-prompts'} aria-controls="ai-tool-prompts-panel" tabIndex={aiTab === 'tool-prompts' ? 0 : -1}
            onClick={() => { setAiTab('tool-prompts'); setSuccess(''); setError('') }}><Pencil size={13} />工具提示词</button>
        </div>
        <div className="console-notification" role="status" aria-live="polite">{error ? <span className="console-error">{error}</span> : success ? <span className="console-success"><CheckCircle2 size={14} />{success}</span> : null}</div>
        {aiTab === 'models' && <section id="ai-model-panel" className="console-section" role="tabpanel" aria-labelledby="ai-tab-models">
          <div className="console-section-heading"><h2 id="providers-heading">模型连接</h2><span>{config?.providers.length ?? 0} 个连接</span></div>
          {!config && !error ? <div className="console-empty"><LoaderCircle size={22} className="spin" /><p>正在读取配置</p></div> : config?.providers.length ? <div className="provider-grid">{config.providers.map(provider => <article className="provider-card" key={provider.id}>
            <div className="provider-card-heading"><span className="provider-symbol"><Cpu size={19} strokeWidth={1.5} /></span><div><h3>{provider.name}</h3><span className="provider-model">{provider.model}</span></div>
              {config.default_provider_id === provider.id && <span className="default-badge"><Star size={10} fill="currentColor" />默认</span>}</div>
            <div className="provider-url" title={provider.base_url}>{provider.base_url}</div>
            <div className="provider-key-state"><KeyRound size={11} />{provider.has_api_key ? '密钥已配置' : '未配置密钥'}<span className={provider.enabled ? 'provider-enabled' : ''}>{provider.enabled ? '已启用' : '已停用'}</span></div>
            {testResults[provider.id] && <div className={`provider-test-result${testResults[provider.id].success ? ' success' : ''}`} role="status">{testResults[provider.id].message}</div>}
            <div className="provider-card-actions"><button type="button" className="console-ghost test-provider" onClick={() => { void testProvider(provider.id) }} disabled={!!testing || !provider.enabled}>
              {testing === provider.id ? <LoaderCircle size={13} className="spin" /> : <Play size={12} />} {testing === provider.id ? '测试中' : '测试连接'}</button>
              <div className="provider-icon-actions">{config.default_provider_id !== provider.id && provider.enabled && <button type="button" className="chat-header-action" title="设为默认" aria-label={`将 ${provider.name} 设为默认`} onClick={() => { void makeDefault(provider.id) }}><Star size={14} /></button>}
                <button type="button" className="chat-header-action" title="编辑" aria-label={`编辑 ${provider.name}`} onClick={() => edit(provider)}><Pencil size={14} /></button>
                <button type="button" className="chat-header-action" title="删除" aria-label={`删除 ${provider.name}`} onClick={() => setConfirmDelete(provider.id)}><Trash2 size={14} /></button></div>
            </div>
          </article>)}</div> : <div className="console-empty"><span className="console-empty-icon"><Cpu size={25} strokeWidth={1.4} /></span><h3>添加第一个 AI</h3><p>填写 Base URL、API Key 和模型，即可开始对话。</p><button type="button" className="console-ghost" onClick={() => edit()}><Plus size={14} />添加连接</button></div>}
        </section>}

        {aiTab === 'tools' && <ToolsHelp />}
        {aiTab === 'tool-prompts' && <ToolPrompts />}
        {aiTab === 'prompt' && <form id="ai-prompt-panel" className="console-section prompt-section" role="tabpanel" aria-labelledby="ai-tab-prompt" onSubmit={savePrompt}>
          <div className="console-section-heading"><h2>全局提示词</h2><span>对所有用户生效</span></div>
          <p className="console-section-description">设置 AI 的身份、语气与回复规则。</p>
          <label className="sr-only" htmlFor="global-prompt">全局提示词</label>
          <textarea id="global-prompt" value={prompt} maxLength={20000} rows={7} disabled={!config || savingPrompt} onChange={event => { setPrompt(event.target.value); setSuccess('') }} placeholder="例如：你是我们的业务助手，请使用简洁、准确的中文回答。" />
          <div className="prompt-footer"><span>{prompt.length.toLocaleString()} / 20,000</span><button className="console-primary" type="submit" disabled={!config || savingPrompt || prompt === config.global_prompt}>
            {savingPrompt ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存提示词</button></div>
        </form>}
      </div>
    </div>

    {form && <div className="console-modal-backdrop" onPointerDown={event => { if (event.target === event.currentTarget && !saving) setForm(null) }}>
      <section className="console-modal" role="dialog" aria-modal="true" aria-labelledby="provider-form-title">
        <div className="console-modal-heading"><div><span className="console-eyebrow">MODEL CONNECTION</span><h2 id="provider-form-title">{form.id ? '编辑 AI' : '添加 AI'}</h2></div><button type="button" className="chat-header-action" aria-label="关闭配置窗口" disabled={saving} onClick={() => setForm(null)}><X size={18} /></button></div>
        <form onSubmit={saveProvider} noValidate>
          <div className="console-form-grid"><div className="console-field"><label htmlFor="provider-name">名称</label><input ref={nameInput} id="provider-name" value={form.name} maxLength={80} disabled={saving} placeholder="为这个 AI 起个名字" onChange={event => setForm({ ...form, name: event.target.value })} /></div>
            <div className="console-field"><label htmlFor="provider-model">模型</label><input id="provider-model" value={form.model} maxLength={160} disabled={saving} placeholder="供应商提供的模型 ID" onChange={event => setForm({ ...form, model: event.target.value })} /></div></div>
          <div className="console-field"><label htmlFor="provider-base-url">Base URL</label><input id="provider-base-url" value={form.base_url} maxLength={1024} disabled={saving} placeholder="https://api.example.com/v1" onChange={event => setForm({ ...form, base_url: event.target.value })} /><span className="console-field-hint">支持 Chat Completions 兼容接口。</span></div>
          <div className="console-field"><label htmlFor="provider-api-key">API Key{form.id && <span>留空保持原密钥</span>}</label><div className="console-key-input"><input id="provider-api-key" type={showKey ? 'text' : 'password'} value={form.api_key} maxLength={4096} disabled={saving} autoComplete="off" spellCheck={false} placeholder={form.id ? '已配置 · 不显示原密钥' : '输入 API Key'} onChange={event => setForm({ ...form, api_key: event.target.value })} /><button type="button" aria-label={showKey ? '隐藏 API Key' : '显示 API Key'} onClick={() => setShowKey(value => !value)}>{showKey ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></div>
          <div className="console-form-grid"><div className="console-field"><label htmlFor="provider-max-tokens">最大输出 Token（可留空）</label><input type="number" id="provider-max-tokens" min={1} placeholder="供应商默认" value={form.max_tokens ?? ''} disabled={saving} onChange={event => setForm({ ...form, max_tokens: event.target.value === '' ? null : Number(event.target.value) })} /></div>
            <label className="console-toggle"><input type="checkbox" checked={form.enabled} disabled={saving} onChange={event => setForm({ ...form, enabled: event.target.checked })} /><span><Check size={12} /></span>启用此连接</label></div>
          <div className="console-form-error" role="status">{formError}</div>
          <div className="console-modal-footer"><button type="button" className="console-ghost" disabled={saving} onClick={() => setForm(null)}>取消</button><button type="submit" className="console-primary" disabled={saving}>{saving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存配置</button></div>
        </form>
      </section>
    </div>}
    {confirmDelete && <div className="console-modal-backdrop"><section className="console-modal confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-title"><h2 id="delete-title">删除这个 AI 连接？</h2><p>使用此连接的历史对话会保留。</p><div className="console-modal-footer"><button type="button" className="console-ghost" disabled={!!deleting} onClick={() => setConfirmDelete(null)}>取消</button><button type="button" className="console-primary danger" disabled={!!deleting} onClick={() => { void removeProvider(confirmDelete) }}>{deleting ? '正在删除' : '确认删除'}</button></div></section></div>}
  </section>
}
