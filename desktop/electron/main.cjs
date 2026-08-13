const { app, BrowserWindow, ipcMain, Menu, shell } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs/promises')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const statusFile = argumentValue('--status-file')
const devtools = process.argv.includes('--devtools')
const iconPath = path.join(__dirname, 'assets', 'orin-logo.png')
let mainWindow = null
let appUrl = null
let closing = false
let retrying = false
let lifecycleTimer = null

if (!app.requestSingleInstanceLock()) {
  app.quit()
}

app.on('second-instance', () => {
  if (!mainWindow) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
})

app.whenReady().then(async () => {
  if (process.argv.includes('--focus-only')) {
    app.quit()
    return
  }
  // Orin owns its navigation through the application UI. The stock Electron
  // File/Edit/View bar is both redundant and visually disconnected from it.
  Menu.setApplicationMenu(null)
  registerIpc()
  mainWindow = new BrowserWindow({
    width: 1120,
    height: 760,
    minWidth: 900,
    minHeight: 620,
    show: false,
    backgroundColor: '#070611',
    title: 'Orin',
    icon: iconPath,
    autoHideMenuBar: true,
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: '#0b0a14',
      symbolColor: '#f5f1ff',
      height: 40,
    },
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  protectNavigation(mainWindow)
  mainWindow.on('close', (event) => {
    if (closing) return
    event.preventDefault()
    closeWindow()
  })
  await mainWindow.loadFile(path.join(__dirname, 'splash.html'))
  mainWindow.show()
  if (devtools) mainWindow.webContents.openDevTools({ mode: 'detach' })
})

app.on('window-all-closed', () => app.quit())

function registerIpc() {
  ipcMain.handle('desktop:startup-status', (event) => fromSplash(event) ? readStatus() : null)
  ipcMain.handle('desktop:load-app', async (event, url) => {
    if (!fromSplash(event)) return false
    if (!isLocalOrinUrl(url)) return false
    appUrl = url
    await mainWindow.loadURL(url)
    watchLauncherLifecycle()
    return true
  })
  ipcMain.handle('desktop:open-logs', async (event) => {
    if (!fromSplash(event)) return ''
    const status = await readStatus()
    if (!status || typeof status.logs_dir !== 'string') return ''
    return shell.openPath(status.logs_dir)
  })
  ipcMain.handle('desktop:retry', (event) => fromSplash(event) ? retryStartup() : false)
  ipcMain.handle('desktop:close', (event) => {
    if (!fromSplash(event)) return false
    closeWindow()
    return true
  })
}

async function readStatus() {
  if (!statusFile) return null
  try {
    return JSON.parse(await fs.readFile(statusFile, 'utf8'))
  } catch {
    return null
  }
}

async function retryStartup() {
  if (retrying) return false
  const status = await readStatus()
  if (!status || !Array.isArray(status.restart_command) || status.restart_command.length === 0) return false
  if (!status.restart_command.every((part) => typeof part === 'string' && part.length > 0)) return false
  retrying = true
  try {
    const [executable, ...arguments] = status.restart_command
    const child = spawn(executable, [...arguments, '--desktop', '--desktop-reuse'], {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    })
    child.unref()
    return true
  } catch {
    retrying = false
    return false
  }
}

function protectNavigation(window) {
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (isLocalOrinUrl(url)) {
      window.loadURL(url)
    } else {
      shell.openExternal(url)
    }
    return { action: 'deny' }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if (isLocalOrinUrl(url)) return
    event.preventDefault()
    shell.openExternal(url)
  })
}

function isLocalOrinUrl(value) {
  try {
    const url = new URL(value)
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1') return false
    return appUrl === null || url.origin === new URL(appUrl).origin
  } catch {
    return false
  }
}

async function closeWindow() {
  if (closing) return
  closing = true
  await requestShutdown()
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy()
  app.quit()
}

function watchLauncherLifecycle() {
  if (lifecycleTimer) return
  lifecycleTimer = setInterval(async () => {
    const status = await readStatus()
    if (!status || status.mode !== 'stopped' || closing) return
    closing = true
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy()
    app.quit()
  }, 700)
}

async function requestShutdown() {
  const status = await readStatus()
  if (status && typeof status.shutdown_file === 'string') {
    try {
      await fs.writeFile(status.shutdown_file, `${process.pid} ${new Date().toISOString()}\n`, 'utf8')
    } catch {
      // The launcher may already have exited. Its normal logs remain the source
      // of diagnostic detail; closing a window must never be blocked by it.
    }
  }
}

function fromSplash(event) {
  return event.senderFrame.url === pathToFileURL(path.join(__dirname, 'splash.html')).href
}

function argumentValue(name) {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] : null
}
