const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('desktop', {
  clipboard: {
    writeText: value => ipcRenderer.invoke('clipboard:write-text', value),
  },
  service: {
    request: (action, value) => ipcRenderer.invoke('service:request', action, value),
    onUploadProgress: callback => {
      const listener = (_event, progress) => callback(progress)
      ipcRenderer.on('upload:progress', listener)
      return () => ipcRenderer.removeListener('upload:progress', listener)
    },
  },
  auth: {
    current: () => ipcRenderer.invoke('auth:current'),
    login: value => ipcRenderer.invoke('auth:login', value),
    logout: () => ipcRenderer.invoke('auth:logout'),
  },
  preferences: {
    read: () => ipcRenderer.invoke('preferences:read'),
    write: value => ipcRenderer.invoke('preferences:write', value),
  },
  minimize: () => ipcRenderer.send('window:minimize'),
  maximize: () => ipcRenderer.send('window:maximize'),
  close: () => ipcRenderer.send('window:close'),
  openExternal: url => ipcRenderer.invoke('link:open', url),
})
