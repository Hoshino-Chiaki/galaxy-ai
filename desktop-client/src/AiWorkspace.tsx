import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { CalendarClock, ArrowUp, BrainCircuit, ChevronDown, Download, File, FileSpreadsheet, History, LoaderCircle, Plus, Sparkles, Square, SquarePen, SquareTerminal, Trash2, UserRound, X } from 'lucide-react'
import { service } from './service'
import MarkdownMessage from './components/MarkdownMessage'
import PendingReply, { formatDuration } from './components/PendingReply'
import ClarificationPanel from './components/ClarificationPanel'
import UploadRequestCard from './components/UploadRequestCard'
import ChatAttachmentCard from './components/ChatAttachment'
import type { ScheduleTask, ScheduleIntent } from './components/TaskSchedule'
import './components/schedule.css'

type Props = { user: CurrentUser; error: string; active: boolean; scheduleIntent?: ScheduleIntent | null }
type Availability = { available: boolean; name: string | null; model: string | null; thinking_controls?: boolean }
type Effort = 'low' | 'high' | 'max'
type ThinkingPreferences = { thinking: boolean; effort: Effort }

function readThinkingPreferences(key: string): ThinkingPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}')
    return { thinking: value.thinking !== false, effort: ['low', 'high', 'max'].includes(value.effort) ? value.effort : 'high' }
  } catch { return { thinking: true, effort: 'high' } }
}

export default function AiWorkspace({ user, error, active, scheduleIntent }: Props) {
  const [linkedTask, setLinkedTask] = useState<ScheduleTask | null>(null)
  const consumedIntent = useRef('')
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState('')
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<ConversationSummary[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [conversationLoading, setConversationLoading] = useState<string | null>(null)
  const conversationLoadRef = useRef(0)
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [availability, setAvailability] = useState<Availability | null>(null)
  const [revealingMessageId, setRevealingMessageId] = useState<string | null>(null)
  const [pending, setPending] = useState<string | null>(null)
  const [pendingProgress, setPendingProgress] = useState<ChatProgress | null>(null)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const uploadingRef = useRef(false)
  const [attachments, setAttachments] = useState<ChatAttachment[]>([])
  const [pendingFiles, setPendingFiles] = useState<ChatAttachment[]>([])
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)
  const [stopping, setStopping] = useState(false)
  const request = useRef<{ id: string; stopping: boolean; startedAt: number } | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)
  const preferenceKey = 'galaxy.thinking.' + user.workspace_id + '.' + user.id
  const [thinkingPreferences, setThinkingPreferences] = useState<ThinkingPreferences>(() => readThinkingPreferences(preferenceKey))
  const [failure, setFailure] = useState<{ question: string; message: string; uploadId?: string; files?: ChatAttachment[] } | null>(null)
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

  useEffect(() => {
    if (!active || !scheduleIntent || consumedIntent.current === scheduleIntent.nonce || busy || uploading || conversationLoading) return
    consumedIntent.current = scheduleIntent.nonce
    setLinkedTask(scheduleIntent.task || null)
    setDraft(''); setAttachments([]); setFailure(null); setNotice(''); setHistoryOpen(false)
    if (scheduleIntent.conversationId) { void openConversation(scheduleIntent.conversationId); return }
    setConversation(null)
    if (!scheduleIntent.task) setNotice('输入任务内容和触发时间，例如：每天早上九点，整理昨天指定店铺的经营日报。实验阶段结果保存在 Galaxy AI。')
    focusEditor()
  }, [active, scheduleIntent, busy, uploading, conversationLoading])

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
  useEffect(() => window.desktop?.service.onUploadProgress(value => {
    if (uploadingRef.current) setUploadProgress(value)
  }), [])

  async function attachFiles() {
    if (busyRef.current || uploadingRef.current || conversationLoading) return
    uploadingRef.current = true; setUploading(true); setUploadProgress(null); setNotice('')
    try {
      const result = await service<{ files: ChatAttachment[]; failures: { filename: string; message: string }[]; canceled?: boolean }>('storage.upload')
      if (alive.current && !result.canceled) {
        setAttachments(previous => [...previous, ...result.files])
        if (result.failures.length) setNotice(result.failures.map(file => `${file.filename}：${file.message}`).join('\n'))
      }
    } catch (reason) { if (alive.current) setNotice((reason as Error).message) }
    finally {
      uploadingRef.current = false
      if (alive.current) { setUploading(false); setUploadProgress(null); focusEditor() }
    }
  }

  useEffect(() => {
    const current = request.current
    if (!busy || !current) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const progress = await service<ChatProgress>('chat.progress', { request_id: current!.id })
        if (!cancelled && request.current?.id === current!.id) setPendingProgress(progress)
      } catch { /* Send may not have reached the server yet; the send request owns errors. */ }
      if (!cancelled) timer = setTimeout(() => { void poll() }, 1000)
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [busy])

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

  const followReply = useCallback(() => {
    const element = scroll.current
    if (element && element.scrollHeight - element.clientHeight - element.scrollTop < 120) element.scrollTop = element.scrollHeight
  }, [])

  useEffect(() => { followReply() }, [pendingProgress, followReply])

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
    if (busyRef.current || uploadingRef.current || conversationLoading) return
    setLinkedTask(null); setAttachments([]); setRevealingMessageId(null); setConversation(null); setDraft(''); setNotice(''); setFailure(null); setHistoryOpen(false); focusEditor()
  }

  async function openConversation(id: string) {
    if (busyRef.current || uploadingRef.current || conversationLoading) return
    const sequence = ++conversationLoadRef.current
    setRevealingMessageId(null); setConversationLoading(id); setConversation(null); setFailure(null); setNotice('')
    try {
      const value = await service<Conversation>('chat.get', { id })
      if (alive.current && conversationLoadRef.current === sequence) {
        setConversation(value); setDraft(''); setAttachments([]); setHistoryOpen(false); focusEditor()
      }
    } catch (reason) {
      if (alive.current && conversationLoadRef.current === sequence) setNotice('打开对话失败：' + (reason as Error).message)
    } finally {
      if (alive.current && conversationLoadRef.current === sequence) setConversationLoading(null)
    }
  }

  async function deleteConversation(id: string) {
    if (busyRef.current || uploadingRef.current || !window.confirm('删除这段对话？')) return
    try {
      await service('chat.delete', { id })
      if (!alive.current) return
      if (conversation?.id === id) newConversation()
      await loadHistory()
    } catch (reason) { if (alive.current) setNotice((reason as Error).message) }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    await submitText(draft.trim() || (attachments.length ? '请读取并分析这些附件。' : ''), undefined, attachments)
  }

  async function submitText(text: string, uploadId?: string, files: ChatAttachment[] = []) {
    if (!text.trim() || busyRef.current || uploadingRef.current) return
    if (availability?.available === false) {
      setNotice(user.permissions.includes('ai:configure') ? '请先在控制台添加并启用 AI 连接。' : 'AI 尚未配置，请联系管理员。')
      return
    }
    const original = text
    const requestId = crypto.randomUUID()
    request.current = { id: requestId, stopping: false, startedAt: performance.now() }
    busyRef.current = true
    setRevealingMessageId(null); setPendingProgress(null); setBusy(true); setStopping(false); setNotice(''); setFailure(null); setPending(original.trim()); setPendingFiles(files); setAttachments([]); setDraft(''); setHistoryOpen(false)
    try {
      const result = await service<{ conversation: Conversation; interrupted?: boolean }>('chat.send', {
        interactive_questions: true, message: original.trim(), conversation_id: conversation?.id ?? null, request_id: requestId,
        ...(uploadId ? { upload_id: uploadId } : {}),
        ...(files.length ? { file_ids: files.map(file => file.id) } : {}),
        ...(linkedTask ? { schedule_task_id: linkedTask.id } : {}),
        ...(availability?.thinking_controls ? { thinking: thinkingPreferences.thinking, reasoning_effort: thinkingPreferences.effort } : {}),
      })
      if (alive.current) { setLinkedTask(null); setRevealingMessageId(result.conversation.messages.filter(item => item.role === 'assistant').at(-1)?.id ?? null); setConversation(result.conversation); window.dispatchEvent(new Event('galaxy-usage-updated')); setNotice(result.interrupted ? '已打断' : ''); void loadHistory().catch(() => {}) }
    } catch (reason) {
      if (alive.current) setFailure({ question: original.trim(), message: (reason as Error).message, uploadId, files })
    } finally {
      busyRef.current = false
      if (request.current?.id === requestId) request.current = null
      if (alive.current) { setBusy(false); setStopping(false); setPending(null); setPendingFiles([]); focusEditor() }
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

  const clarification = busy && !stopping ? pendingProgress?.interaction : null
  async function answerClarification(answers: { question_id: string; option_id?: string; text?: string }[]) {
    const current = request.current
    if (!current || !clarification) throw new Error('当前询问已结束。')
    await service('chat.answer', { request_id: current.id, interaction_id: clarification.id, answers })
  }

  const composer = <div className="composer-block">
    {linkedTask && <div className="schedule-linked" aria-label="关联任务"><CalendarClock size={17} /><div><strong>{linkedTask.name}</strong><span>修改触发时间 · 当前：{linkedTask.time_description}</span><code>{linkedTask.id}</code></div><button type="button" aria-label="取消关联任务" disabled={busy} onClick={() => setLinkedTask(null)}><X size={14} /></button></div>}
    {clarification ? <ClarificationPanel key={clarification.id} request={clarification} stopping={stopping} onSubmit={answerClarification} onCancel={() => { void stopReply() }} /> : <form className={'chat-composer' + (busy ? ' is-busy' : '')} onSubmit={send} aria-busy={busy}>
      {!!attachments.length && <div className="composer-attachments" aria-label="待发送附件">{attachments.map(file => <ChatAttachmentCard key={file.id} file={file} disabled={busy || uploading} onRemove={() => setAttachments(previous => previous.filter(item => item.id !== file.id))} />)}</div>}
      {uploading && <div className="composer-uploading" role="status"><LoaderCircle size={13} className="spin" /><span>{uploadProgress?.filename || '正在选择文件…'}</span>{uploadProgress && <><progress max={100} value={uploadProgress.percent} /><small>{uploadProgress.percent}%</small><button type="button" aria-label="取消文件上传" onClick={() => { void service('storage.cancel', { id: uploadProgress.upload_request_id }) }}><X size={13} /></button></>}</div>}
      <label className="sr-only" htmlFor="chat-message">消息</label>
      <span className="sr-only" id="composer-hint">Enter 发送，Shift + Enter 换行</span>
      <textarea ref={editor} id="chat-message" name="message" placeholder={linkedTask ? '输入新的执行时间，例如：每周五下午六点' : '有什么想聊的？'} rows={1} maxLength={4000}
        value={draft} disabled={busy || uploading} onChange={event => { setDraft(event.target.value); setNotice('') }} onKeyDown={onEditorKeyDown}
        aria-describedby="composer-hint" />
      <div className="composer-toolbar">
        <div className="composer-settings">{user.permissions.includes('task:upload') && user.permissions.includes('file:read') && <button type="button" className="composer-add-file" disabled={busy || uploading || !!conversationLoading} aria-label="上传附件" title="上传文件到本人的 AI 文件存储" onClick={() => { void attachFiles() }}><Plus size={18} strokeWidth={1.6} /></button>}<span className="composer-mode"><Sparkles size={13} strokeWidth={1.6} />{availability?.name || 'AI 对话'}</span>
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
          </button> : <button type="submit" className="send-message" aria-label="发送消息" title="发送消息" disabled={(!draft.trim() && !attachments.length) || uploading}>
            <ArrowUp size={19} strokeWidth={1.9} />
          </button>}
        </div>
      </div>
    </form>}
    <div className="chat-notice" role="status" aria-live="polite">{error || notice}</div>
  </div>

  function messageRow(message: ChatMessage) {
    const assistant = message.role === 'assistant'
    const reasoning = message.reasoning_content?.trim()
    const tools = message.tool_events ?? []
    return <article key={message.id} className={'chat-message ' + (assistant ? 'assistant' : 'user')}>
      <span className="message-avatar">{assistant ? <img src="./ai.png" alt="AI 头像" /> : user.avatar ? <img src={user.avatar} alt="你的头像" /> : <UserRound size={16} strokeWidth={1.5} />}</span>
      <div className="message-body"><span className="message-author">{assistant ? 'AI' : '你'}
        {assistant && message.elapsed_ms != null && <span className="message-duration">用时 {formatDuration(message.elapsed_ms)}</span>}</span>
        {assistant && (reasoning || tools.length > 0) && <details className="reasoning-disclosure message-process">
          <summary><BrainCircuit size={13} strokeWidth={1.6} /><span>{reasoning ? '思考过程' : '执行过程'}</span>
            {reasoning && message.model_elapsed_ms != null && <span className="process-duration">思考与生成 {formatDuration(message.model_elapsed_ms)}</span>}
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
        {assistant ? <MarkdownMessage content={message.content} progressive={message.id === revealingMessageId} onRevealProgress={followReply} /> : <div className="message-text">{message.content}</div>}
        {!assistant && !!message.attached_files?.length && <div className="message-attachments">{message.attached_files.map(file => <ChatAttachmentCard key={file.id} file={file} />)}</div>}
        {assistant && message.usage && <div className={'message-token-usage' + (!message.usage.complete ? ' incomplete' : '')} title="包含本轮工具往返、思考和上下文处理产生的模型请求用量，不等同于可见正文的字数。">{!message.usage.requests ? 'Token 用量未返回' : <>本次输出 {message.usage.unknown_requests ? '≥ ' : ''}{message.usage.output_tokens.toLocaleString()} Token · 输入 {message.usage.input_tokens.toLocaleString()} · 合计 {message.usage.total_tokens.toLocaleString()}{message.usage.unknown_requests ? ' · 部分用量未确认' : ''}</>}</div>}
        {assistant && tools.filter(event => event.status === 'success' && event.command === 'galaxy-cli task request-upload').map(event => {
          const invitation = event.result as typeof event.result & { upload_request_id?: string; purpose?: string }
          if (!invitation.upload_request_id || !invitation.purpose) return null
          return <UploadRequestCard key={event.id} id={invitation.upload_request_id} purpose={invitation.purpose}
            disabled={busy || uploading} used={!!conversation?.messages.some(item => item.uploaded_file?.file_id === invitation.upload_request_id)}
            onBusyChange={value => { uploadingRef.current = value; if (alive.current) setUploading(value) }}
            onContinue={file => submitText(`已上传“${file.filename}”，请按文件用途继续处理。`, file.file_id)} />
        })}
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
      <div className="chat-page-actions" aria-label="对话操作">
        <button type="button" className="chat-header-action" title="新建对话" aria-label="新建对话" disabled={busy || uploading} onClick={newConversation}><SquarePen size={17} strokeWidth={1.6} /></button>
        <button ref={historyButton} type="button" className={'chat-header-action' + (historyOpen ? ' selected' : '')} title="对话记录" aria-label="对话记录" disabled={busy || uploading}
          aria-expanded={historyOpen} aria-controls="conversation-history" onClick={() => setHistoryOpen(value => !value)}><History size={18} strokeWidth={1.6} /></button>
      </div>

    {!hasMessages ? <div className="conversation-layout"><div className="chat-content"><div className="chat-start">
      {conversationLoading ? <div className="conversation-loading" role="status"><LoaderCircle size={22} className="spin" /><span>正在打开对话</span><small>正在读取完整消息，请稍候</small></div> : <div className="welcome" aria-labelledby="welcome-heading">
        <div className="welcome-symbol ai-welcome-avatar"><img src="./ai.png" alt="AI 头像" /></div>
        <h1 id="welcome-heading">Hello <span>{user.name || user.username}</span><span className="greeting-dot">.</span></h1>
        <p>从一个问题，开始今天的灵感。</p>
      </div>}
    </div></div><div className="conversation-composer">{composer}</div></div> : <div className="conversation-layout">
      <div className="message-scroll" ref={scroll}><div className="message-list" role="log" aria-label="聊天消息" aria-live="polite">
        {conversation?.messages.map(messageRow)}
        {failure && <>
          {messageRow({ id: 'failed-user', role: 'user', content: failure.question, created_at: 0, attached_files: failure.files })}
          <div className="chat-failure" role="alert"><strong>这次处理未完成</strong><p>{failure.message}</p>
            <div><button type="button" onClick={() => { void submitText(failure.question, failure.uploadId, failure.files) }}>重试</button>
              <button type="button" onClick={() => { setDraft(failure.question); setAttachments(failure.files || []); setFailure(null); focusEditor() }}>编辑问题</button></div>
          </div>
        </>}
        {pending !== null && <>
          {messageRow({ id: 'pending-user', role: 'user', content: pending, created_at: 0, attached_files: pendingFiles })}
          <article className="chat-message assistant pending-reply"><span className="message-avatar"><img src="./ai.png" alt="AI 头像" /></span>
            <div className="message-body"><span className="message-author">AI</span>
              {request.current && <details key={request.current.id} className="reasoning-disclosure message-process" open>
                <summary><BrainCircuit size={13} strokeWidth={1.6} /><span>{thinkingPreferences.thinking ? '思考过程' : '执行过程'}</span><ChevronDown className="reasoning-chevron" size={12} /></summary>
                <div className="message-process-content">
                  {pendingProgress?.reasoning_content && <div className="reasoning-text">{pendingProgress.reasoning_content}</div>}
                  <div role="status">{stopping ? '正在停止回复' : pendingProgress?.phase === 'waiting' ? '等待你的选择' : pendingProgress?.phase === 'tool' ? `正在执行${pendingProgress.tool_name ? '：' + pendingProgress.tool_name : '工具'}` : pendingProgress?.phase === 'summarizing' ? '正在整理上下文' : pendingProgress?.phase === 'model' ? '正在思考' : pendingProgress?.phase === 'finishing' ? '正在整理回答' : '正在准备'}</div>
                </div>
              </details>}
              <PendingReply active={active} stopping={stopping} />
            </div>
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
          <button type="button" className="history-open" disabled={!!conversationLoading} onClick={() => { void openConversation(item.id) }}><span>{item.title}</span><small>{new Date(item.updated_at * 1000).toLocaleDateString()} · {item.model}</small></button>
          <button type="button" className="history-delete chat-header-action" aria-label={'删除对话 ' + item.title} title="删除对话" onClick={() => { void deleteConversation(item.id) }}><Trash2 size={13} /></button>
        </div>)}
      </div> : <div className="history-empty"><History size={27} strokeWidth={1.3} /><p>暂无对话记录</p></div>}
    </aside>}
  </section>
}
