const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('orinDesktop', Object.freeze({
  isDesktop: true,
  startupStatus: () => ipcRenderer.invoke('desktop:startup-status'),
  loadApp: (url) => ipcRenderer.invoke('desktop:load-app', url),
  openLogs: () => ipcRenderer.invoke('desktop:open-logs'),
  retry: () => ipcRenderer.invoke('desktop:retry'),
  close: () => ipcRenderer.invoke('desktop:close'),
  onUpdateAvailable: (callback) => {
    const listener = (_event, update) => callback(update)
    ipcRenderer.on('desktop:update-available', listener)
    return () => ipcRenderer.removeListener('desktop:update-available', listener)
  },
  runUpdate: () => ipcRenderer.invoke('desktop:run-update'),
}))
