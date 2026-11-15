const { app, BrowserWindow, ipcMain, screen, shell, clipboard } = require('electron')
const path = require('node:path')
const fs = require('node:fs/promises')
const { pathToFileURL } = require('node:url')
const authentication = require('./auth.cjs')

// Retain the existing DPAPI/session store across the product rename.
app.setPath('userData', path.join(app.getPath('appData'), '星序 AI'))

let mainWindow
const entryPath = path.join(__dirname, '../dist/index.html')
const entryUrl = pathToFileURL(entryPath).href

function trusted(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame?.url !== entryUrl)
    throw new Error('Untrusted IPC sender')
}

function settingsFile() { return path.join(app.getPath('userData'), 'login-preferences.json') }

app.whenReady().then(async () => {
  const bounds = screen.getPrimaryDisplay().workAreaSize
  mainWindow = new BrowserWindow({
    width: Math.min(1280, bounds.width), height: Math.min(820, bounds.height),
    minWidth: 760, minHeight: 660, frame: false, show: false,
    title: 'galaxy ai', backgroundColor: '#111111', autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true,
      autoplayPolicy: 'no-user-gesture-required',
    },
  })
  ipcMain.handle('preferences:read', async event => {
    trusted(event)
    try {
      const value = JSON.parse(await fs.readFile(settingsFile(), 'utf8'))
      return { remember: value.remember === true,
        account: value.remember === true && typeof value.account === 'string' ? value.account.slice(0, 160) : '' }
    } catch { return { remember: false, account: '' } }
  })
  let writes = Promise.resolve()
  ipcMain.handle('preferences:write', async (event, value) => {
    trusted(event)
    if (!value || typeof value.remember !== 'boolean' || typeof value.account !== 'string' || value.account.length > 160)
      throw new Error('Invalid preferences')
    const preferences = { remember: value.remember, account: value.remember ? value.account.trim() : '' }
    const operation = writes.catch(() => {}).then(async () => {
      await fs.mkdir(app.getPath('userData'), { recursive: true })
      const temporary = settingsFile() + '.tmp'
      await fs.writeFile(temporary, JSON.stringify(preferences), { mode: 0o600 })
      await fs.rename(temporary, settingsFile())
    })
    writes = operation
    return operation
  })
  ipcMain.on('window:minimize', event => { trusted(event); mainWindow.minimize() })
  ipcMain.handle('service:request', (event, action, value) => {
    trusted(event)
    return authentication.api(action, value)
  })
  ipcMain.handle('clipboard:write-text', (event, value) => {
    trusted(event)
    if (typeof value !== 'string') throw new Error('Clipboard content must be text')
    clipboard.writeText(value)
  })
  for (const action of ['current', 'login', 'logout']) {
    ipcMain.handle(`auth:${action}`, (event, value) => {
      trusted(event)
      return authentication.invoke(action, value)
    })
  }
  ipcMain.on('window:maximize', event => { trusted(event); mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize() })
  ipcMain.on('window:close', event => { trusted(event); mainWindow.close() })
  ipcMain.handle('link:open', (event, value) => {
    trusted(event)
    if (typeof value !== 'string' || value.length > 8192) throw new Error('Invalid link')
    const url = new URL(value)
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('Invalid link')
    return shell.openExternal(url.href)
  })
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  mainWindow.webContents.on('will-navigate', (event, url) => { if (url !== entryUrl) event.preventDefault() })
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false))
  mainWindow.once('ready-to-show', () => mainWindow.show())
  await mainWindow.loadFile(entryPath)
})

app.on('window-all-closed', () => app.quit())
if (!app.requestSingleInstanceLock()) app.quit()
app.on('second-instance', () => { if (mainWindow) { mainWindow.restore(); mainWindow.focus() } })
