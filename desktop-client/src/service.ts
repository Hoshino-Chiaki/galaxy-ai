export async function service<T>(action: string, value?: unknown): Promise<T> {
  if (!window.desktop) throw new Error('请在 Windows 客户端中操作。')
  const result = await window.desktop.service.request<T>(action, value)
  if (!result.ok) throw new Error(result.message)
  return result.data
}
