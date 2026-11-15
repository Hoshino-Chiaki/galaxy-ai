/** Desktop copy uses the native clipboard through the trusted preload bridge. */
export async function copyText(value: string): Promise<void> {
  if (window.desktop?.clipboard) {
    await window.desktop.clipboard.writeText(value)
    return
  }
  if (!navigator.clipboard?.writeText) throw new Error('当前环境无法复制，请重试。')
  await navigator.clipboard.writeText(value)
}
