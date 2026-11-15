// Operator migration: business tool contracts belong in tool descriptions.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')
const expected = `你是 Galaxy AI 业务助手。当前北京时间：{{current_time}}；今天：{{today}}；昨天：{{yesterday}}。根据用户请求调用工具获取实际数据，完成分析，并在用户需要时生成可下载附件。经营报表需区分今天截至查询时刻的数据和昨天全天数据，按接口的金额单位、分页和合计行含义分析，避免重复累加合计行。生成文件后先取得相对 path，再调用 file.publish，取得真实 file_id，再告知用户从附件下载。若某一步失败，说明已完成和未完成的部分，不编造结果。

【文件生成与托管】
excel.create 只生成文件，返回当前账号工作目录内的相对 path、filename、size、sheets。需要给用户下载时，再调用 galaxy-cli file publish --input JSON，仅传入 {"path":"生成工具实际返回的相对路径"}。file.publish 返回真实 file_id、filename、size、media_type、download_path 后，聊天框才显示下载附件。不要把生成路径当成下载链接；仅使用工具实际返回的路径和附件信息。已发布附件无需刷新服务。`
const prompt = '你是 Galaxy AI 业务助手。当前北京时间：{{current_time}}；今天：{{today}}；昨天：{{yesterday}}。根据用户请求调用工具获取实际数据，完成分析，并在用户需要时生成可下载附件。若某一步失败，说明已完成和未完成的部分，不编造结果。'

;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:19224')
  try {
    const page = browser.contexts()[0].pages().find(page => page.url().includes('index.html'))
    const original = await page.evaluate(async () => {
      const result = await window.desktop.service.request('admin.settings')
      if (!result.ok) throw new Error('Cannot read configuration')
      return result.data.global_prompt
    })
    const normalize = value => value.replace(/\\r\\n|\\n|\\r/g, '\n').replace(/\r\n/g, '\n').trim()
    assert(original === prompt || normalize(original) === expected, 'Prompt changed; preserve unexpected instructions')
    if (original !== prompt) {
      const directory = path.resolve(__dirname, '../../backend/private/prompt-backups')
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
      fs.writeFileSync(path.join(directory, `before-tool-description-separation-${Date.now()}.json`), JSON.stringify({ global_prompt: original }), { mode: 0o600, flag: 'wx' })
      await page.evaluate(async ({ original, prompt }) => {
        const fresh = await window.desktop.service.request('admin.settings')
        if (!fresh.ok || fresh.data.global_prompt !== original) throw new Error('Configuration changed during update')
        const saved = await window.desktop.service.request('admin.prompt', { prompt })
        if (!saved.ok) throw new Error('Cannot save prompt')
      }, { original, prompt })
    }
    const verified = await page.evaluate(async prompt => {
      const settings = await window.desktop.service.request('admin.settings')
      const catalog = await window.desktop.service.request('admin.tools')
      if (!settings.ok || !catalog.ok) throw new Error('Verification failed')
      const description = name => catalog.data.data.find(tool => tool.tool === name)?.description || ''
      return { promptMatches: settings.data.global_prompt === prompt,
        reportRulesInHelp: description('youzan.business-report').includes('统计时长不同'),
        fileRulesInHelp: description('file.publish').includes('源文件路径不是下载链接'),
        excelWorkflowInHelp: description('excel.create').includes('再调用 file.publish'),
        toolCount: catalog.data.data.length }
    }, prompt)
    assert(verified.promptMatches && verified.reportRulesInHelp && verified.fileRulesInHelp && verified.excelWorkflowInHelp)
    fs.writeFileSync(path.resolve(__dirname, '../artifacts/global-prompt-separation-check.json'), JSON.stringify({ verified: true, ...verified, globalPrompt: prompt }, null, 2))
    console.log(JSON.stringify({ verified: true, ...verified }))
  } finally { await browser.close() }
})().catch(error => { console.error(error.message); process.exitCode = 1 })
