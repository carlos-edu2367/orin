const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('orinDesktop', Object.freeze({
  isDesktop: true,
  startupStatus: () => ipcRenderer.invoke('desktop:startup-status'),
  loadApp: (url) => ipcRenderer.invoke('desktop:load-app', url),
  openLogs: () => ipcRenderer.invoke('desktop:open-logs'),
  retry: () => ipcRenderer.invoke('desktop:retry'),
  close: () => ipcRenderer.invoke('desktop:close'),
}))
