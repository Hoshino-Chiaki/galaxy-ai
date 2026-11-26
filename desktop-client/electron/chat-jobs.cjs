// A POST is retried only with the same server-idempotent request identifier.
const wait = ms => new Promise(resolve => setTimeout(resolve, ms))
function transient(error) {
  return error?.status >= 500 || error?.status === 429 || error?.retryable === true
    || ['ECONNRESET', 'EPIPE', 'ETIMEDOUT', 'ESOCKETTIMEDOUT', 'ECONNREFUSED', 'ENETUNREACH', 'EHOSTUNREACH', 'EAI_AGAIN', 'ENOTFOUND'].includes(error?.code)
}
async function followJob({ request, isCurrent, sleep = wait }, identifier) {
  for (;;) {
    if (!isCurrent()) throw new Error('账号已切换，请重新操作。')
    let state
    try { state = await request('GET', `/api/v1/chat/runs/${identifier}`, null, 15000) }
    catch (error) { if (!transient(error)) throw error; await sleep(1500); continue }
    if (['failed', 'interrupted'].includes(state.status)) throw new Error(`${state.error?.message || '服务器任务已中断。'}（任务编号：${identifier}）`)
    if (state.status === 'completed') {
      try { return await request('GET', `/api/v1/chat/jobs/${identifier}/result`, null, 15000) }
      catch (error) { if (!transient(error)) throw error; await sleep(1500); continue }
    }
    await sleep(1000)
  }
}
async function submitJob(options, body) {
  const identifier = body?.request_id
  if (!/^[0-9a-f-]{36}$/.test(identifier || '')) throw new Error('任务编号无效。')
  for (;;) {
    if (!options.isCurrent()) throw new Error('账号已切换，请重新操作。')
    try { await options.request('POST', '/api/v1/chat/jobs', body, 15000); break }
    catch (error) {
      if (!transient(error)) throw error
      try { await options.request('GET', `/api/v1/chat/runs/${identifier}`, null, 15000); break }
      catch (statusError) { if (statusError.status && statusError.status !== 404 && !transient(statusError)) throw statusError }
      await (options.sleep || wait)(1500)
    }
  }
  return followJob(options, identifier)
}
module.exports = { submitJob, followJob }
