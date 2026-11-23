import { Check, Plus, Sparkles, Wrench } from 'lucide-react'

type Group = { title: string; items: string[] }
export function parseReleaseNotes(notes: string): Group[] {
  const groups: Group[] = []
  let current: Group = { title: '更新内容', items: [] }
  for (const line of notes.split(/\r?\n/)) {
    const text = line.trim()
    if (!text) continue
    const heading = text.match(/^#{1,3}\s+(.+)$/)
    if (heading) {
      if (current.items.length) groups.push(current)
      current = { title: heading[1], items: [] }
    } else current.items.push(text.replace(/^(?:[-*•]|\d+[.)、])\s*/, ''))
  }
  if (current.items.length) groups.push(current)
  return groups
}
function RichText({ text }: { text: string }) {
  return <>{text.split(/(\*\*[^*]+\*\*)/g).map((part, index) => part.startsWith('**') && part.endsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part)}</>
}
export default function ReleaseNotes({ notes }: { notes: string }) {
  const groups = parseReleaseNotes(notes)
  return <div className="release-notes">{groups.map((group, index) => {
    const kind = /新增|新功能/.test(group.title) ? 'added' : /修复|问题/.test(group.title) ? 'fixed' : /优化|改进/.test(group.title) ? 'improved' : 'general'
    const Icon = kind === 'added' ? Plus : kind === 'fixed' ? Wrench : Sparkles
    return <section className={'release-note-group ' + kind} key={index}><h3><span><Icon size={13} aria-hidden="true" /></span>{group.title}</h3><ul>{group.items.map((item, itemIndex) => <li key={itemIndex}><Check size={12} aria-hidden="true" /><span><RichText text={item} /></span></li>)}</ul></section>
  })}</div>
}
export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KiB`
  return `${(value / 1024 / 1024).toFixed(1)} MiB`
}
export function remainingTime(seconds: number | null | undefined) {
  if (seconds == null) return '正在估算剩余时间'
  if (seconds < 60) return `约 ${Math.max(1, seconds)} 秒`
  return `约 ${Math.ceil(seconds / 60)} 分钟`
}
