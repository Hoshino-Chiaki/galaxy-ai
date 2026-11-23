import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, CircleHelp, LoaderCircle, Square } from 'lucide-react'
import './clarification.css'
type Answer = { question_id: string; option_id?: string; text?: string }
type Props = { request: ClarificationRequest; stopping: boolean; onSubmit: (answers: Answer[]) => Promise<void>; onCancel: () => void }
export default function ClarificationPanel({ request, stopping, onSubmit, onCancel }: Props) {
  const [index, setIndex] = useState(0)
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [posting, setPosting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const question = request.questions[index]
  const choice = choices[question.id]
  const valid = Boolean(choice && (choice !== '__custom__' || texts[question.id]?.trim()))
  const disabled = posting || submitted || stopping
  useEffect(() => { heading.current?.focus() }, [index])
  async function next() {
    if (!valid || disabled) return
    if (index < request.questions.length - 1) { setIndex(index + 1); return }
    setPosting(true); setError('')
    try {
      await onSubmit(request.questions.map(q => choices[q.id] === '__custom__'
        ? { question_id: q.id, text: texts[q.id].trim() }
        : { question_id: q.id, option_id: choices[q.id] }))
      setSubmitted(true)
    } catch (reason) { setError((reason as Error).message) }
    finally { setPosting(false) }
  }
  return <section className="clarification-panel" role="dialog" aria-modal="false" aria-labelledby="clarification-title" aria-busy={posting}>
    <header className="clarification-header"><span><CircleHelp size={15} aria-hidden="true" />需要你确认</span><span>{index + 1} / {request.questions.length}</span></header>
    <h2 ref={heading} tabIndex={-1} id="clarification-title">{question.title}</h2>
    <form onSubmit={event => { event.preventDefault(); void next() }}>
      <fieldset disabled={disabled} className="clarification-options"><legend className="sr-only">{question.title}</legend>
        {question.options.map((option, ordinal) => <label className={'clarification-option' + (choice === option.id ? ' selected' : '')} key={option.id}>
          <input type="radio" name={question.id} value={option.id} checked={choice === option.id} onChange={() => { setChoices(v => ({ ...v, [question.id]: option.id })); setError('') }} />
          <span className="clarification-number" aria-hidden="true">{ordinal + 1}</span>
          <span className="clarification-option-text"><strong>{option.label}</strong>{option.description && <small>{option.description}</small>}</span>
          <span className="clarification-selected" aria-hidden="true">{choice === option.id && <Check size={14} />}</span>
        </label>)}
        <label className={'clarification-option' + (choice === '__custom__' ? ' selected' : '')}>
          <input type="radio" name={question.id} value="__custom__" checked={choice === '__custom__'} onChange={() => { setChoices(v => ({ ...v, [question.id]: '__custom__' })); setError('') }} />
          <span className="clarification-number" aria-hidden="true">…</span><span className="clarification-option-text"><strong>自行填写</strong></span>
          <span className="clarification-selected" aria-hidden="true">{choice === '__custom__' && <Check size={14} />}</span>
        </label>
        {choice === '__custom__' && <label className="clarification-custom"><span className="sr-only">填写你的答案</span>
          <textarea autoFocus value={texts[question.id] || ''} maxLength={4000} rows={2} placeholder="填写你的选择或补充说明" onChange={event => setTexts(v => ({ ...v, [question.id]: event.target.value }))} /></label>}
      </fieldset>
      {error && <p className="clarification-error" role="alert">{error}</p>}
      <footer className="clarification-footer">
        <button type="button" className="clarification-cancel" disabled={stopping || posting} onClick={onCancel}><Square size={11} aria-hidden="true" />{stopping ? '正在取消' : '取消任务'}</button>
        <div>{index > 0 && <button type="button" className="clarification-back" disabled={disabled} onClick={() => setIndex(index - 1)}><ArrowLeft size={13} aria-hidden="true" />上一题</button>}
          <button type="submit" className="clarification-submit" disabled={!valid || disabled}>
            {posting || submitted ? <LoaderCircle className="spin" size={14} aria-hidden="true" /> : null}
            {submitted ? '已提交，继续处理中' : posting ? '提交中' : index < request.questions.length - 1 ? '下一题' : '确认并继续'}
            {!posting && !submitted && <ArrowRight size={14} aria-hidden="true" />}</button></div>
      </footer>
    </form>
  </section>
}
