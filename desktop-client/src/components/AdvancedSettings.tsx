import { useEffect, useState, type FormEvent } from 'react'
import { LoaderCircle, Save } from 'lucide-react'
import { service } from '../service'

type Settings = { input_token_budget: number; summary_tokens: number; recent_turns: number; model_response_mb: number; tool_output_mb: number; history_response_mb: number }
const fields: { key: keyof Settings; label: string; min: number; max: number; hint: string }[] = [
  { key: 'input_token_budget', label: '上下文预算（Token）', min: 4096, max: 1000000, hint: '每次发送给模型的消息、工具定义和工具结果预算。' },
  { key: 'summary_tokens', label: '历史摘要预算（Token）', min: 256, max: 8192, hint: '上下文过长时自动压缩历史所允许的摘要长度。' },
  { key: 'recent_turns', label: '保留最近轮数', min: 1, max: 20, hint: '历史整理时优先保留的最近对话轮数。' },
  { key: 'model_response_mb', label: '模型响应上限（MB）', min: 2, max: 32, hint: '模型返回正文、思考和工具调用 JSON 的最大大小。' },
  { key: 'tool_output_mb', label: '工具输出上限（MB）', min: 1, max: 32, hint: 'Galaxy CLI 单次工具返回内容的最大大小。' },
  { key: 'history_response_mb', label: '客户端数据响应上限（MB）', min: 2, max: 64, hint: '聊天历史、人员列表等客户端接口返回内容的最大大小。' },
]
export default function AdvancedSettings() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let valid = true
    void service<{ advanced: Settings }>('admin.settings').then(value => { if (valid) setSettings(value.advanced) })
      .catch(reason => { if (valid) setNotice((reason as Error).message) })
    return () => { valid = false }
  }, [])
  async function save(event: FormEvent) {
    event.preventDefault(); if (!settings || saving) return
    setSaving(true); setNotice('')
    try { setSettings(await service<Settings>('admin.advanced', settings)); setNotice('高级设置已保存，新的对话请求立即生效。') }
    catch (reason) { setNotice((reason as Error).message) }
    finally { setSaving(false) }
  }
  return <form id="ai-advanced-panel" className="console-section" role="tabpanel" aria-labelledby="ai-tab-advanced" onSubmit={save}>
    <div className="console-section-heading"><h2>高级设置</h2><span>服务器级策略</span></div>
    <p className="console-section-description">调整上下文整理、模型响应、工具输出和聊天历史读取。</p>
    {settings && <><div className="advanced-grid">{fields.map(field => <label className="console-field" key={field.key}>
      <span>{field.label}</span><input type="number" min={field.min} max={field.max} step={1} required value={settings[field.key]} disabled={saving}
        onChange={event => setSettings({ ...settings, [field.key]: Number(event.target.value) })} /><small>{field.hint}</small>
    </label>)}</div><div className="prompt-footer"><span>保存后对新请求生效。</span><button type="submit" className="console-primary" disabled={saving}>{saving ? <LoaderCircle size={14} className="spin" /> : <Save size={14} />}保存高级设置</button></div></>}
    <p className="console-section-description" role="status">{notice}</p>
  </form>
}
