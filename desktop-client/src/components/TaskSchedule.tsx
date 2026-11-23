import { useCallback, useEffect, useState } from 'react'
import { CalendarClock, ChevronRight, Clock3, FlaskConical, LoaderCircle, MessageSquare, Pencil, Plus, RefreshCw, Trash2, X } from 'lucide-react'
import { service } from '../service'
import Nav from 'react-bootstrap/Nav'
import './personnel.css'
import './schedule.css'

export type ScheduleTask = { id: string; revision: string; name: string; instruction: string; time_description: string; rule: { type: string; timezone: string }; status: string; next_at: number | null; next_runs: number[]; last_run: { status: string; started_at: number; finished_at: number | null; error: string | null; conversation_id: string; result_saved?: boolean } | null }
export type ScheduleIntent = { nonce: string; task?: ScheduleTask; conversationId?: string }
const time = (stamp: number | null, zone = 'Asia/Shanghai') => stamp ? new Intl.DateTimeFormat('zh-CN', { timeZone: zone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(stamp * 1000)) : '—'
const statuses: Record<string, string> = { scheduled: '等待触发', running: '执行中', completed: '已结束', success: '已完成', failed: '未完成', partial: '部分完成', interrupted: '已中断' }

export default function TaskSchedule({ user, onChat }: { user: CurrentUser; onChat: (intent: ScheduleIntent) => void }) {
  const [tasks, setTasks] = useState<ScheduleTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState<string | null>(null)
  const [confirmation, setConfirmation] = useState<ScheduleTask | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try { const result = await service<{ tasks: ScheduleTask[] }>('schedule.list'); setTasks(result.tasks); setError('') }
    catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => {
    let alive = true, inFlight = false
    async function poll() {
      if (inFlight) return
      inFlight = true
      try { const result = await service<{ tasks: ScheduleTask[] }>('schedule.list'); if (alive) { setTasks(result.tasks); setError('') } }
      catch (reason) { if (alive) setError((reason as Error).message) }
      finally { inFlight = false; if (alive) setLoading(false) }
    }
    void poll(); const timer = setInterval(() => { void poll() }, 5000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  useEffect(() => {
    if (!confirmation) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !deleting) setConfirmation(null) }
    document.addEventListener('keydown', close); return () => document.removeEventListener('keydown', close)
  }, [confirmation, deleting])
  async function remove(task: ScheduleTask) {
    setDeleting(task.id); setError('')
    try { await service('schedule.delete', { id: task.id, revision: task.revision }); setTasks(value => value.filter(item => item.id !== task.id)); setConfirmation(null) }
    catch (reason) { setError((reason as Error).message); await load(true) }
    finally { setDeleting(null) }
  }
  const canCreate = user.permissions.includes('schedule:create') && user.permissions.includes('ai:chat')
  return <section className="console-panel schedule-panel" aria-label="任务调度">
    <header className="console-topbar"><div className="console-breadcrumb">任务调度 <span className="schedule-experiment">实验</span></div><button className="console-ghost" type="button" onClick={() => { void load() }} disabled={loading} aria-label="刷新任务"><RefreshCw size={14} className={loading ? 'spin' : ''} /></button></header>
    <div className="console-scroll"><div className="console-body schedule-body">
      <p className="schedule-intro">实验功能 · 任务由服务器执行，结果保存在 Galaxy AI，关闭客户端不影响运行。</p>
      <div className="schedule-section-head"><span>{tasks.length} 个任务 <i />按当前账号独立保存</span>{canCreate && <button type="button" className="console-primary" onClick={() => onChat({ nonce: crypto.randomUUID() })}><Plus size={14} />让 AI 创建</button>}</div>
      <div role="status" aria-live="polite" className="schedule-error">{error}</div>
      {loading && !tasks.length ? <div className="schedule-empty"><LoaderCircle className="spin" size={24} /><p>正在读取任务</p></div> : !tasks.length ? <div className="schedule-empty"><Clock3 size={35} strokeWidth={1.2} /><h2>暂无定时任务</h2><p>告诉 AI 要做什么、何时执行。任务创建成功后会出现在这里。</p>{canCreate && <button type="button" className="console-ghost" onClick={() => onChat({ nonce: crypto.randomUUID() })}>去 AI 安排任务<ChevronRight size={14} /></button>}</div> : <div className="schedule-grid">
        {tasks.map((task, index) => <div key={task.id} className="schedule-entry">
          <Nav fill className={'employee-row department-row schedule-row tone-' + ['cyan', 'amber', 'blue', 'teal'][index % 4]} aria-label={task.name + '的定时任务'}>
            <button type="button" className="employee-row-identity department-row-identity schedule-identity" aria-expanded={expanded === task.id} onClick={() => setExpanded(value => value === task.id ? null : task.id)}>
              <span className="department-card-icon"><CalendarClock size={21} strokeWidth={1.4} /></span><span className="department-row-summary"><strong title={task.name}>{task.name}</strong><span>{task.time_description} · {statuses[task.status] || task.status}</span><span>下次：{time(task.next_at, task.rule.timezone)}</span></span>
            </button>
            <Nav.Link as="button" type="button" eventKey="time" className="employee-row-details" disabled={!user.permissions.includes('schedule:update') || !user.permissions.includes('ai:chat')} onClick={() => onChat({ nonce: crypto.randomUUID(), task })}><Pencil size={13} />修改触发时间</Nav.Link>
            <Nav.Link as="button" type="button" eventKey="delete" className="employee-row-permissions" disabled={!user.permissions.includes('schedule:delete')} onClick={() => setConfirmation(task)} aria-label={'删除任务 ' + task.name}><Trash2 size={13} />删除</Nav.Link>
          </Nav>
          {expanded === task.id && <div className="schedule-detail"><p>{task.instruction}</p><span>{task.rule.timezone}</span>{!!task.next_runs.length && <details className="schedule-preview"><summary>接下来三次触发</summary><ol>{task.next_runs.map(stamp => <li key={stamp}>{new Intl.DateTimeFormat('zh-CN', { timeZone: task.rule.timezone, dateStyle: 'medium', timeStyle: 'short', hour12: false }).format(new Date(stamp * 1000))}</li>)}</ol></details>}</div>}
          <div className="schedule-last">{task.last_run ? <><span>最近运行 · {statuses[task.last_run.status] || task.last_run.status} · {time(task.last_run.started_at, task.rule.timezone)}</span>{task.last_run.error && <small>{task.last_run.error}</small>}{task.last_run.result_saved && <button type="button" onClick={() => onChat({ nonce: crypto.randomUUID(), conversationId: task.last_run!.conversation_id })}><MessageSquare size={12} />查看结果</button>}</> : <span>尚未执行</span>}</div>
        </div>)}
      </div>}
      <p className="schedule-footnote"><FlaskConical size={13} />实验阶段：每次运行重新核验你的权限与渠道授权；服务中断的运行不自动重放，避免重复操作。请在运行结果中核对实际完成情况。</p>
    </div></div>
    {confirmation && <div className="schedule-modal-backdrop" onClick={() => { if (!deleting) setConfirmation(null) }}><div className="schedule-modal" role="dialog" aria-modal="true" aria-labelledby="schedule-delete-title" onClick={event => event.stopPropagation()}><button className="schedule-modal-close" type="button" disabled={!!deleting} onClick={() => setConfirmation(null)} aria-label="关闭删除确认"><X size={16} /></button><h2 id="schedule-delete-title">删除这个任务？</h2><p>{confirmation.name}</p><small>后续不再触发，正在执行的任务会请求停止。已完成的业务操作不会撤回，历史结果对话保留。</small><div><button className="console-ghost" type="button" disabled={!!deleting} onClick={() => setConfirmation(null)}>保留任务</button><button className="schedule-delete-confirm" type="button" disabled={!!deleting} onClick={() => { void remove(confirmation) }}>{deleting ? <LoaderCircle size={14} className="spin" /> : <Trash2 size={14} />}删除任务</button></div></div></div>}
  </section>
}
