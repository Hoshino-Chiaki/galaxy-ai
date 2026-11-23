import { useCallback, useEffect, useState } from 'react'
import { LoaderCircle, RefreshCw, Wallet } from 'lucide-react'
import { service } from '../service'

type Balance = { is_available: boolean; checked_at: number; balance_infos: { currency: string; total_balance: string; granted_balance: string; topped_up_balance: string }[] }

export default function DeepseekBalance({ id }: { id: string }) {
  const [value, setValue] = useState<Balance | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const query = useCallback(() => service<Balance>('admin.providerBalance', { id }), [id])
  useEffect(() => {
    let alive = true
    setLoading(true); setError('')
    void query().then(result => { if (alive) setValue(result) }).catch(reason => { if (alive) { setError((reason as Error).message); setValue(null) } }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [query, refresh])
  return <div className="provider-balance" aria-label="DeepSeek 余额">
    <div className="provider-balance-label"><span><Wallet size={12} />账户余额</span><button type="button" aria-label="刷新 DeepSeek 余额" title="刷新余额" disabled={loading} onClick={() => setRefresh(v => v + 1)}>{loading ? <LoaderCircle size={12} className="spin" /> : <RefreshCw size={12} />}</button></div>
    <div role="status" aria-live="polite">{loading ? <span className="provider-balance-hint">正在查询</span> : error ? <span className="provider-balance-error">{error}</span> : value && <>{value.balance_infos.map(item => <div key={item.currency} className="provider-balance-value"><strong>{item.currency === 'CNY' ? '¥' : '$'} {item.total_balance}</strong><span>充值 {item.topped_up_balance} · 赠送 {item.granted_balance}</span></div>)}<span className={'provider-balance-hint' + (!value.is_available ? ' insufficient' : '')}>{value.is_available ? '余额可用' : '余额不足'} · {new Date(value.checked_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })} 更新</span></>}</div>
  </div>
}
