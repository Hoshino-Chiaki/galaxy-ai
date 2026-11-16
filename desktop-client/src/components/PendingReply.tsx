import { useEffect, useState } from 'react'
import { BrainCircuit, Check, Clock3, LoaderCircle, Sparkles, SquareTerminal } from 'lucide-react'
import { service } from '../service'

export function formatDuration(milliseconds: number) {
  const seconds = Math.max(0, milliseconds) / 1000
  if (seconds < 1) return '不到 1 秒'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`
}

function clock(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`
}

type Props = { requestId: string; startedAt: number; thinking: boolean; stopping: boolean }

export default function PendingReply({ requestId, startedAt, thinking, stopping }: Props) {
  const [now, setNow] = useState(() => performance.now())
  const [snapshot, setSnapshot] = useState<{ progress: ChatProgress; receivedAt: number } | null>(null)
  const [progressUnavailable, setProgressUnavailable] = useState(false)

  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), 250)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    let valid = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const progress = await service<ChatProgress>('chat.progress', { request_id: requestId })
        if (valid) {
          setSnapshot({ progress, receivedAt: performance.now() })
          setProgressUnavailable(false)
        }
      } catch {
        // A request can still be registering, or its final response can already be
        // in transit. A failed progress read must never interrupt the chat itself.
        if (valid && performance.now() - startedAt > 5000) setProgressUnavailable(true)
      } finally {
        if (valid) timer = setTimeout(() => { void poll() }, 1000)
      }
    }
    timer = setTimeout(() => { void poll() }, 300)
    return () => { valid = false; clearTimeout(timer) }
  }, [requestId, startedAt])

  const progress = snapshot?.progress
  const sinceSnapshot = Math.max(0, now - (snapshot?.receivedAt ?? now))
  const stale = progressUnavailable || (!!snapshot && sinceSnapshot > 6000)
  const totalMs = Math.max(now - startedAt, progress ? progress.elapsed_ms + sinceSnapshot : 0)
  const modelMs = (progress?.model_elapsed_ms ?? 0) + (progress?.phase === 'model' && !stale ? sinceSnapshot : 0)
  const phase = stopping ? 'stopping' : stale ? 'waiting' : progress?.phase ?? 'preparing'
  const inModel = phase === 'model'
  const title = phase === 'stopping' ? '正在停止回复'
    : phase === 'summarizing' ? '正在整理对话上下文'
    : phase === 'tool' ? progress?.tool_name || '正在使用工具'
    : phase === 'finishing' ? '正在完成回复'
    : inModel ? thinking ? '正在思考与生成' : '正在生成回复'
    : phase === 'waiting' ? '正在等待回复' : '正在准备回答'
  const detail = phase === 'stopping' ? '正在结束当前任务，已完成的结果会保留。'
    : phase === 'summarizing' ? '整理较早的对话，保留你的目标和关键约束。'
    : phase === 'tool' ? '正在执行这一步，结果返回后会继续整理回答。'
    : phase === 'finishing' ? '回答已生成，正在保存本次对话。'
    : phase === 'waiting' ? '进度暂时不可用，回复仍在等待中。'
    : inModel && (progress?.tools_completed ?? 0) > 0 ? '正在结合工具结果，继续整理回答。'
    : inModel ? thinking ? '正在整理思路，准备回答你的问题。' : '正在组织回答，请稍候。'
    : '正在读取这段对话的上下文。'
  const Icon = phase === 'tool' ? SquareTerminal : phase === 'summarizing' ? Sparkles
    : phase === 'stopping' || phase === 'waiting' ? LoaderCircle : BrainCircuit

  return <div className="pending-status-card" data-phase={phase} aria-live="off">
    <div className="pending-status-top">
      <span className="pending-phase-symbol" aria-hidden="true"><Icon size={20} strokeWidth={1.5} /></span>
      <div className="pending-phase-copy">
        <span className="pending-phase-title" role="status" aria-live="polite">{title}</span>
        <p>{detail}</p>
      </div>
      <div className="pending-clock" aria-live="off">
        <span>{inModel ? thinking ? '思考与生成' : '生成用时' : '已用时'}</span>
        <time>{clock(inModel ? modelMs : totalMs)}</time>
      </div>
    </div>
    <div className="pending-activity-track" aria-hidden="true"><i /></div>
    <div className="pending-status-footer">
      <span><Clock3 size={12} />总用时 <time aria-live="off">{clock(totalMs)}</time></span>
      {(progress?.tools_completed ?? 0) > 0 && <span><Check size={12} />已处理 {progress?.tools_completed} 次工具调用</span>}
      {(progress?.model_calls ?? 0) > 1 && <span>第 {progress?.model_calls} 轮{thinking ? '思考' : '生成'}</span>}
      {!stopping && !progress?.tools_completed && <span className="pending-stop-hint">可随时停止回复</span>}
    </div>
  </div>
}
