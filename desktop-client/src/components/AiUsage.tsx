import { useEffect, useMemo, useState } from 'react'
import { Activity, ArrowDownLeft, ArrowUpRight, ChartNoAxesCombined, ChevronDown, LoaderCircle, RefreshCw, UserRound } from 'lucide-react'
import { service } from '../service'
import './usage.css'

type Counts = { input_tokens: number; output_tokens: number; total_tokens: number; cache_hit_tokens: number; cache_miss_tokens: number; requests: number; unknown_requests: number; failed_requests: number }
type Report = { start: string; end: string; totals: Counts; users: (Counts & { user_id: string; account: string; name: string; avatar: string | null; department: string })[]; daily: (Counts & { date: string })[]; sources: (Counts & { source: string })[]; models: (Counts & { model: string })[]; checked_at: number }
const labels: Record<string, string> = { desktop: '桌面端', wechat: '微信', feishu: '飞书', schedule: '定时任务', test: '连接测试' }
const number = (value: number) => value.toLocaleString('zh-CN')
const compact = (value: number) => value >= 1000000 ? (value / 1000000).toFixed(2) + 'M' : value >= 1000 ? (value / 1000).toFixed(1) + 'K' : String(value)
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
const shift = (day: string, count: number) => { const value = new Date(day + 'T12:00:00Z'); value.setUTCDate(value.getUTCDate() + count); return value.toISOString().slice(0, 10) }

function Trend({ data, first, last }: { data: Report['daily']; first: string; last: string }) {
  const points = useMemo(() => {
    const days = Math.round((Date.parse(last) - Date.parse(first)) / 86400000) + 1
    const buckets = Math.min(days, 90)
    const step = Math.max(1, Math.ceil(days / buckets))
    const values = Array.from({ length: Math.ceil(days / step) }, (_, index) => ({ date: shift(first, index * step), total: 0 }))
    for (const row of data) { const index = Math.floor((Date.parse(row.date) - Date.parse(first)) / 86400000 / step); if (values[index]) values[index].total += row.total_tokens }
    return values
  }, [data, first, last])
  const max = Math.max(1, ...points.map(p => p.total)), width = 760, height = 150
  const coordinates = points.map((point, index) => [36 + (points.length > 1 ? index / (points.length - 1) : .5) * (width - 60), 20 + (1 - point.total / max) * (height - 40)])
  const path = coordinates.map(([x, y], index) => `${index ? 'L' : 'M'} ${x} ${y}`).join(' ')
  return <div className="usage-trend-plot"><div className="usage-trend-range"><span>{compact(max)}</span><span>Token</span></div><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${first} 至 ${last} Token 用量趋势`}>
    <defs><linearGradient id="usage-line" x1="0" x2="1"><stop stopColor="#61b8c4" /><stop offset="1" stopColor="#a69bcf" /></linearGradient><linearGradient id="usage-area" x1="0" y1="0" x2="0" y2="1"><stop stopColor="#69b6c1" stopOpacity=".22" /><stop offset="1" stopColor="#69b6c1" stopOpacity="0" /></linearGradient></defs>
    {[20, 65, 110].map(y => <line key={y} x1="28" x2="745" y1={y} y2={y} stroke="#ffffff0b" strokeDasharray="3 6" />)}
    {coordinates.length > 1 && <path d={`${path} L ${coordinates.at(-1)![0]} ${height - 10} L ${coordinates[0][0]} ${height - 10} Z`} fill="url(#usage-area)" />}
    <path d={path} fill="none" stroke="url(#usage-line)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
    {coordinates.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={points.length > 45 ? 2 : 3.5} fill="#9dcbd2"><title>{points[i].date} · {number(points[i].total)} Token</title></circle>)}
  </svg><div className="usage-trend-dates"><span>{first}</span><span>{last}</span></div></div>
}

export default function AiUsage() {
  const [range, setRange] = useState(() => ({ start: today().slice(0, 8) + '01', end: today() }))
  const [preset, setPreset] = useState('month'), [source, setSource] = useState('')
  const [data, setData] = useState<Report | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    if (!range.start || !range.end || range.start > range.end) { setError('请选择有效的日期范围。'); setData(null); setLoading(false); return }
    let alive = true
    setLoading(true); setError(''); setData(null)
    void service<Report>('usage.report', { ...range, ...(source ? { entry_point: source } : {}) }).then(value => { if (alive) setData(value) }).catch(reason => { if (alive) setError((reason as Error).message) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [range.start, range.end, source, refresh])
  function choose(value: string) {
    const now = today()
    setPreset(value)
    setRange({ start: value === 'today' ? now : value === 'week' ? shift(now, -6) : now.slice(0, 8) + '01', end: now })
  }
  const max = Math.max(1, ...(data?.users.map(u => u.input_tokens + u.output_tokens) || []))
  return <section className="console-panel usage-panel" aria-label="AI 用量统计"><header className="usage-header"><div><ChartNoAxesCombined size={17} /><span>AI 用量</span><small>按 Galaxy 账号归账</small></div><button className="console-ghost" type="button" aria-label="刷新 AI 用量" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={14} className={loading ? 'spin' : ''} /></button></header>
    <div className="console-scroll"><div className="console-body usage-body"><div className="usage-filters"><div className="usage-presets">{[['today', '今天'], ['week', '近 7 天'], ['month', '本月']].map(([key, text]) => <button key={key} type="button" className={preset === key ? 'selected' : ''} onClick={() => choose(key)}>{text}</button>)}</div><div className="usage-date"><input type="date" aria-label="用量开始日期" value={range.start} max={range.end} onChange={event => { setPreset('custom'); setRange(value => ({ ...value, start: event.target.value })) }} /><span>—</span><input type="date" aria-label="用量结束日期" value={range.end} min={range.start} onChange={event => { setPreset('custom'); setRange(value => ({ ...value, end: event.target.value })) }} /></div><label className="usage-source"><select aria-label="用量来源" value={source} onChange={event => setSource(event.target.value)}><option value="">全部入口</option>{Object.entries(labels).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select><ChevronDown size={12} /></label></div>
      {error && <div className="usage-error" role="alert">{error}</div>}
      {loading ? <div className="usage-loading" role="status"><LoaderCircle size={23} className="spin" />正在读取实际用量</div> : data && <>
        <div className="usage-metrics"><article><span><Activity size={13} />Token 合计</span><strong>{number(data.totals.total_tokens)}</strong><small>{data.totals.unknown_requests ? `${data.totals.unknown_requests} 次请求用量不完整，已计入可确认部分` : '供应商返回的实际用量'}</small></article><article><span><ArrowDownLeft size={13} />输入 Token</span><strong>{number(data.totals.input_tokens)}</strong><small>已确认缓存命中 {number(data.totals.cache_hit_tokens)}</small></article><article><span><ArrowUpRight size={13} />输出 Token</span><strong>{number(data.totals.output_tokens)}</strong><small>{number(data.totals.requests)} 次模型请求 · {data.totals.failed_requests} 次未完成</small></article></div>
        <div className="usage-chart-card"><div className="usage-section-heading"><h2>使用趋势</h2><span>北京时间 · {data.start} — {data.end}</span></div><Trend data={data.daily} first={data.start} last={data.end} /></div>
        <div className="usage-chart-card usage-ranking"><div className="usage-section-heading"><h2>员工用量</h2><div className="usage-legend"><span><i />输入</span><span><i />输出</span></div></div>
          {!data.totals.requests && <p className="usage-empty">此范围尚无用量记录，以下展示当前员工。</p>}
          <div className="usage-people">{data.users.map((person, index) => <article className="usage-person" key={person.user_id}><span className="usage-rank">{String(index + 1).padStart(2, '0')}</span><span className="usage-avatar">{person.avatar ? <img src={person.avatar} alt="" loading="lazy" /> : <UserRound size={19} strokeWidth={1.5} />}</span><div className="usage-person-data"><div className="usage-person-title"><strong>{person.name}</strong><span>{person.department || person.account}</span><b title="已确认的 Token 合计">{number(person.total_tokens)}<small> Token</small></b></div><div className="usage-bar" role="img" aria-label={`${person.name}，输入 ${person.input_tokens}，输出 ${person.output_tokens} Token`}><span style={{ width: `${person.input_tokens / max * 100}%` }} /><span style={{ width: `${person.output_tokens / max * 100}%` }} /></div><div className="usage-person-detail"><span>输入 {number(person.input_tokens)} · 输出 {number(person.output_tokens)} · {person.requests} 次请求</span>{person.unknown_requests > 0 && <span>{person.unknown_requests} 次用量不完整</span>}</div></div></article>)}</div>
        </div>
        <div className="usage-breakdowns"><div className="usage-chart-card"><div className="usage-section-heading"><h2>入口分布</h2></div>{data.sources.length ? data.sources.map(item => <div className="usage-breakdown" key={item.source}><span>{labels[item.source] || item.source}</span><i><b style={{ width: `${item.total_tokens / Math.max(1, data.totals.total_tokens) * 100}%` }} /></i><strong>{compact(item.total_tokens)}</strong></div>) : <p className="usage-empty">暂无记录</p>}</div><div className="usage-chart-card"><div className="usage-section-heading"><h2>模型用量</h2></div>{data.models.length ? data.models.map(item => <div className="usage-model" key={item.model}><span>{item.model}</span><strong>{number(item.total_tokens)}<small> Token</small></strong></div>) : <p className="usage-empty">暂无记录</p>}</div></div>
        <p className="usage-footnote">仅统计启用后的实际请求，包含工具往返、上下文压缩和已知的中断用量；历史对话不倒推补算。缺失用量单独标记，删除聊天记录不会删除用量账单。{data.checked_at && <span>更新于 {new Date(data.checked_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}</span>}</p>
      </>}
    </div></div>
  </section>
}
