import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ArrowUp, BrainCircuit, ChevronDown, Download, File, FileSpreadsheet, History, LoaderCircle, Sparkles, Square, SquarePen, SquareTerminal, Trash2, UserRound, X } from 'lucide-react'
import { service } from './service'
import MarkdownMessage from './components/MarkdownMessage'
import { ShineBorder } from './components/magicui/ShineBorder'

type Props = { user: CurrentUser; error: string; active: boolean }
type Availability = { available: boolean; name: string | null; model: string | null; thinking_controls?: boolean }
type Effort = 'low' | 'high' | 'max'
type ThinkingPreferences = { thinking: boolean; effort: Effort }

function readThinkingPreferences(key: string): ThinkingPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}')
    return { thinking: value.thinking !== false, effort: ['low', 'high', 'max'].includes(value.effort) ? value.effort : 'high' }
  } catch { return { thinking: true, effort: 'high' } }
}

export default function AiWorkspace({ user, error, active }: Props) {
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<ConversationSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [availability, setAvailability] = useState<Availability | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [stopping, setStopping] = useState(false)
  const request = useRef<{ id: string; stopping: boolean } | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)
  const preferenceKey = 'galaxy.thinking.' + user.workspace_id + '.' + user.id
  const [thinkingPreferences, setThinkingPreferences] = useState<ThinkingPreferences>(() => readThinkingPreferences(preferenceKey))
  const [failure, setFailure] = useState<{ question: string; message: string } | null>(null)
  const busyRef = useRef(false)
  const alive = useRef(true)
  const editor = useRef<HTMLTextAreaElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const historyButton = useRef<HTMLButtonElement>(null)
  const historyClose = useRef<HTMLButtonElement>(null)
  const hasMessages = !!conversation?.messages.length || pending !== null || failure !== null

  useEffect(() => {
    try { localStorage.setItem(preferenceKey, JSON.stringify(thinkingPreferences)) } catch { /* Use in-memory settings if storage is unavailable. */ }
  }, [preferenceKey, thinkingPreferences])

  async function downloadFile(id: string) {
    if (downloading) return
    setDownloading(id); setNotice('')
    try {
      const result = await service<{ saved?: boolean; canceled?: boolean; filename?: string }>('file.download', { id })
      if (alive.current && result.saved) setNotice('已保存 ' + result.filename)
    } catch (reason) { if (alive.current) setNotice((reason as Error).message) }
    finally { if (alive.current) setDownloading(null) }
  }

  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  useEffect(() => {
    if (!active) return
    setAvailability(null)
    let valid = true
    void service<Availability>('chat.status').then(value => { if (valid) setAvailability(value) }).catch(() => {})
    return () => { valid = false }
  }, [active])

  useEffect(() => {
    const element = editor.current
    if (!element) return
    element.style.height = '0px'
    element.style.height = Math.min(Math.max(element.scrollHeight, 28), 144) + 'px'
  }, [draft, hasMessages])

  useEffect(() => {
    if (scroll.current) scroll.current.scrollTo({ top: scroll.current.scrollHeight, behavior: 'smooth' })
  }, [conversation, pending, busy, failure])

  async function loadHistory() {
    const value = await service<{ conversations: ConversationSummary[] }>('chat.list')
    if (alive.current) setHistory(value.conversations)
  }

  useEffect(() => {
    if (!historyOpen) return
    historyClose.current?.focus()
    setHistoryLoading(true)
    void loadHistory().catch(reason => { if (alive.current) setNotice((reason as Error).message) })
      .finally(() => { if (alive.current) setHistoryLoading(false) })
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') { setHistoryOpen(false); historyButton.current?.focus() }
    }
    document.addEventListener('keydown', escape)
    return () => document.removeEventListener('keydown', escape)
  }, [historyOpen])

  function focusEditor() { requestAnimationFrame(() => editor.current?.focus()) }
  function newConversation() {
    if (busyRef.current) return
    setConversation(null); setDraft(''); setNotice(''); setFailure(null); setHistoryOpen(false); focusEditor()
  }

  async function openConversation(id: string) {
    if (busyRef.current) return
    try {
      const value = await service<Conversation>('chat.get', { id })
      if (alive.current) { setConversation(value); setDraft(''); setNotice(''); setFailure(null); setHistoryOpen(false); focusEditor() }
    } catch (reason) { if (alive.current) setNotice((reason as Error).message) }
  }

  async function deleteConversation(id: string) {
    if (busyRef.current || !window.confirm('删除这段对话？')) return
    try {
      await service('chat.delete', { id })
      if (!alive.current) return
      if (conversation?.id === id) newConversation()
      await loadHistory()
    } catch (reason) { if (alive.current) setNotice((reason as Error).message) }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    await submitText(draft)
  }

  async function submitText(text: string) {
    if (!text.trim() || busyRef.current) return
    if (availability?.available === false) {
      setNotice(user.permissions.includes('ai:configure') ? '请先在控制台添加并启用 AI 连接。' : 'AI 尚未配置，请联系管理员。')
      return
    }
    const original = text
    const requestId = crypto.randomUUID()
    request.current = { id: requestId, stopping: false }
    busyRef.current = true
    setBusy(true); setStopping(false); setNotice(''); setFailure(null); setPending(original.trim()); setDraft(''); setHistoryOpen(false)
    try {
      const result = await service<{ conversation: Conversation; interrupted?: boolean }>('chat.send', {
        message: original.trim(), conversation_id: conversation?.id ?? null, request_id: requestId,
        ...(availability?.thinking_controls ? { thinking: thinkingPreferences.thinking, reasoning_effort: thinkingPreferences.effort } : {}),
      })
      if (alive.current) { setConversation(result.conversation); setNotice(result.interrupted ? '已打断' : ''); void loadHistory().catch(() => {}) }
    } catch (reason) {
      if (alive.current) setFailure({ question: original.trim(), message: (reason as Error).message })
    } finally {
      busyRef.current = false
      if (request.current?.id === requestId) request.current = null
      if (alive.current) { setBusy(false); setStopping(false); setPending(null); focusEditor() }
    }
  }

  async function stopReply() {
    const current = request.current
    if (!current || current.stopping) return
    current.stopping = true
    setStopping(true); setNotice('')
    try {
      await service('chat.cancel', { request_id: current.id })
    } catch (reason) {
      if (alive.current && request.current?.id === current.id) {
        current.stopping = false; setStopping(false); setNotice('打断请求未成功：' + (reason as Error).message)
      }
    }
  }

  function onEditorKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing || event.keyCode === 229) return
    event.preventDefault()
    event.currentTarget.form?.requestSubmit()
  }

  const composer = <div className="composer-block">
    <form className={'chat-composer' + (busy ? ' is-busy' : '')} onSubmit={send} aria-busy={busy}>
      {busy && <ShineBorder borderWidth={1.7} duration={5} style={{ backgroundImage: 'linear-gradient(110deg, #ff827d, #ffc16e, #f3df86, #86d9aa, #7dcff0, #9a9bf4, #df96d9, #ff827d)' }} />}
      <label className="sr-only" htmlFor="chat-message">消息</label>
      <span className="sr-only" id="composer-hint">Enter 发送，Shift + Enter 换行</span>
      <textarea ref={editor} id="chat-message" name="message" placeholder="有什么想聊的？" rows={1} maxLength={4000}
        value={draft} disabled={busy} onChange={event => { setDraft(event.target.value); setNotice('') }} onKeyDown={onEditorKeyDown}
        aria-describedby="composer-hint" />
      <div className="composer-toolbar">
        <div className="composer-settings"><span className="composer-mode"><Sparkles size={13} strokeWidth={1.6} />{availability?.name || 'AI 对话'}</span>
          {availability?.thinking_controls && <>
            <button type="button" role="switch" aria-label="深度思考" aria-checked={thinkingPreferences.thinking} className={'thinking-toggle' + (thinkingPreferences.thinking ? ' enabled' : '')} disabled={busy}
              onClick={() => setThinkingPreferences(value => ({ ...value, thinking: !value.thinking }))}><BrainCircuit size={13} /><span>深度思考</span><i aria-hidden="true" /></button>
            <label className={'thinking-effort' + (!thinkingPreferences.thinking ? ' inactive' : '')}><span className="sr-only">思考强度</span>
              <select aria-label="思考强度" disabled={busy || !thinkingPreferences.thinking} value={thinkingPreferences.effort} onChange={event => setThinkingPreferences(value => ({ ...value, effort: event.target.value as Effort }))}>
                <option value="low">低</option><option value="high">高</option><option value="max">最高</option>
              </select><ChevronDown size={10} aria-hidden="true" /></label>
          </>}
        </div>
        <div className="composer-actions">
          {draft.length > 3600 && <span className="character-count">{draft.length}/4000</span>}
          {busy ? <button type="button" className="send-message stop-message" aria-label="停止回复" title="停止回复" disabled={stopping} onClick={() => { void stopReply() }}>
            {stopping ? <LoaderCircle className="spin" size={16} /> : <Square size={13} fill="currentColor" strokeWidth={1.5} />}
          </button> : <button type="submit" className="send-message" aria-label="发送消息" title="发送消息" disabled={!draft.trim()}>
            <ArrowUp size={19} strokeWidth={1.9} />
          </button>}
        </div>
      </div>
    </form>
    <div className="chat-notice" role="status" aria-live="polite">{error || notice}</div>
  </div>

  function messageRow(message: ChatMessage) {
    const assistant = message.role === 'assistant'
    const reasoning = message.reasoning_content?.trim()
    const tools = message.tool_events ?? []
    return <article key={message.id} className={'chat-message ' + (assistant ? 'assistant' : 'user')}>
      <span className="message-avatar">{assistant ? <img src="./ai.png" alt="AI 头像" /> : <UserRound size={16} strokeWidth={1.5} />}</span>
      <div className="message-body"><span className="message-author">{assistant ? 'AI' : '你'}</span>
        {assistant && (reasoning || tools.length > 0) && <details className="reasoning-disclosure message-process">
          <summary><BrainCircuit size={13} strokeWidth={1.6} /><span>{reasoning ? '思考过程' : '执行过程'}</span>
            {tools.length > 0 && <span className="process-tool-count">{tools.length} 次工具调用</span>}
            <ChevronDown className="reasoning-chevron" size={12} /></summary>
          <div className="message-process-content">
            {reasoning && <div className="reasoning-text">{message.reasoning_content}</div>}
            {tools.map(event => <details className={`tool-event ${event.status}${event.result.partial ? " partial" : ""}`} key={event.id}>
              <summary><SquareTerminal size={13} strokeWidth={1.6} /><span className="tool-command">{event.command || event.name}</span>
                <span className="tool-event-status">{event.status === 'success' ? event.result.partial ? '部分完成' : '已完成' : event.status === 'cancelled' ? '已打断' : event.status === 'denied' ? '已拒绝' : '执行失败'}</span><ChevronDown size={12} /></summary>
              <pre>{JSON.stringify(event.result, null, 2)}</pre>
            </details>)}
          </div>
        </details>}
        {assistant ? <MarkdownMessage content={message.content} /> : <div className="message-text">{message.content}</div>}
        {assistant && message.tool_events?.filter(event => event.status === 'success' && ['galaxy-cli excel create', 'galaxy-cli file publish'].includes(event.command) && event.result.file_id && event.result.filename).map(event => {
          const file = event.result
          return <div className="chat-file-card" key={event.id}>
            <span className="chat-file-icon">{file.filename?.endsWith('.xlsx') ? <FileSpreadsheet size={23} strokeWidth={1.5} /> : <File size={23} strokeWidth={1.5} />}</span>
            <div className="chat-file-info"><strong>{file.filename}</strong><span>{file.filename?.endsWith('.xlsx') ? 'Excel 工作簿' : (file.filename?.split('.').pop()?.toUpperCase() || '文件')} · {Math.max(1, Math.ceil((file.size ?? 0) / 1024))} KB{file.sheets?.length ? ` · ${file.sheets.length} 个工作表` : ''}</span></div>
            <button type="button" className="chat-file-download" disabled={downloading !== null} aria-label={'下载 ' + file.filename} onClick={() => { void downloadFile(file.file_id!) }}>
              {downloading === file.file_id ? <LoaderCircle className="spin" size={15} /> : <Download size={15} />}<span>{downloading === file.file_id ? '保存中' : '下载'}</span>
            </button>
          </div>
        })}
      </div>
    </article>
  }

  return <section className="chat-panel" aria-label="AI 聊天区域" hidden={!active}>
    <header className="chat-header">
      <div className="chat-breadcrumb"><span>AI</span><span className="breadcrumb-divider">/</span><span className="conversation-title">{conversation?.title || '新对话'}</span></div>
      <div className="chat-header-actions">
        <button type="button" className="chat-header-action" title="新建对话" aria-label="新建对话" disabled={busy} onClick={newConversation}><SquarePen size={17} strokeWidth={1.6} /></button>
        <button ref={historyButton} type="button" className={'chat-header-action' + (historyOpen ? ' selected' : '')} title="对话记录" aria-label="对话记录" disabled={busy}
          aria-expanded={historyOpen} aria-controls="conversation-history" onClick={() => setHistoryOpen(value => !value)}><History size={18} strokeWidth={1.6} /></button>
      </div>
    </header>

    {!hasMessages ? <div className="conversation-layout"><div className="chat-content"><div className="chat-start">
      <div className="welcome" aria-labelledby="welcome-heading">
        <div className="welcome-symbol ai-welcome-avatar"><img src="./ai.png" alt="AI 头像" /></div>
        <h1 id="welcome-heading">Hello <span>{user.username}</span><span className="greeting-dot">.</span></h1>
        <p>从一个问题，开始今天的灵感。</p>
      </div>
    </div></div><div className="conversation-composer">{composer}</div></div> : <div className="conversation-layout">
      <div className="message-scroll" ref={scroll}><div className="message-list" role="log" aria-label="聊天消息" aria-live="polite">
        {conversation?.messages.map(messageRow)}
        {failure && <>
          {messageRow({ id: 'failed-user', role: 'user', content: failure.question, created_at: 0 })}
          <div className="chat-failure" role="alert"><strong>这次处理未完成</strong><p>{failure.message}</p>
            <div><button type="button" onClick={() => { void submitText(failure.question) }}>重试</button>
              <button type="button" onClick={() => { setDraft(failure.question); setFailure(null); focusEditor() }}>编辑问题</button></div>
          </div>
        </>}
        {pending !== null && <>
          {messageRow({ id: 'pending-user', role: 'user', content: pending, created_at: 0 })}
          <article className="chat-message assistant pending-reply"><span className="message-avatar"><img src="./ai.png" alt="AI 头像" /></span>
            <div className="message-body"><span className="message-author">AI</span><div className="typing-indicator" aria-label="正在回复"><span /><span /><span /></div><span className="pending-status">{stopping ? '正在打断，请稍候' : '正在处理，请稍候'}</span></div>
          </article>
        </>}
      </div></div>
      <div className="conversation-composer">{composer}</div>
    </div>}

    {historyOpen && <aside id="conversation-history" className="history-drawer" aria-label="对话记录">
      <div className="history-heading"><h2>对话记录</h2><button ref={historyClose} type="button" className="chat-header-action" aria-label="关闭对话记录"
        onClick={() => { setHistoryOpen(false); historyButton.current?.focus() }}><X size={16} /></button></div>
      {historyLoading ? <div className="history-empty"><LoaderCircle size={22} className="spin" /><p>正在读取</p></div> : history.length ? <div className="history-list">
        {history.map(item => <div className={'history-entry' + (conversation?.id === item.id ? ' current' : '')} key={item.id}>
          <button type="button" className="history-open" onClick={() => { void openConversation(item.id) }}><span>{item.title}</span><small>{new Date(item.updated_at * 1000).toLocaleDateString()} · {item.model}</small></button>
          <button type="button" className="history-delete chat-header-action" aria-label={'删除对话 ' + item.title} title="删除对话" onClick={() => { void deleteConversation(item.id) }}><Trash2 size={13} /></button>
        </div>)}
      </div> : <div className="history-empty"><History size={27} strokeWidth={1.3} /><p>暂无对话记录</p></div>}
    </aside>}
  </section>
}
