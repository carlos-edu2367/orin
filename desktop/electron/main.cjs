const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, Notification, shell, Tray } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs/promises')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const statusFile = argumentValue('--status-file')
const devtools = process.argv.includes('--devtools')
// Started at sign-in: no window, only the tray icon. The launcher keeps running either way.
const startHidden = process.argv.includes('--background')
const iconPath = path.join(__dirname, 'assets', 'orin-logo.png')
let mainWindow = null
let appUrl = null
let closing = false
let retrying = false
let updating = false
let lifecycleTimer = null
let tray = null
let asking = false
let announcedBackground = false

const CLOSE_BEHAVIORS = ['ask', 'background', 'quit']
let preferences = { closeBehavior: 'ask' }

if (!app.requestSingleInstanceLock()) {
  app.quit()
}

app.on('second-instance', () => showWindow())

app.whenReady().then(async () => {
  if (process.argv.includes('--focus-only')) {
    app.quit()
    return
  }
  // Orin owns its navigation through the application UI. The stock Electron
  // File/Edit/View bar is both redundant and visually disconnected from it.
  Menu.setApplicationMenu(null)
  preferences = await loadPreferences()
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
    handleCloseRequest()
  })
  await mainWindow.loadFile(path.join(__dirname, 'splash.html'))
  if (startHidden) createTray()
  else mainWindow.show()
  if (devtools) mainWindow.webContents.openDevTools({ mode: 'detach' })
})

app.on('window-all-closed', () => app.quit())

function registerIpc() {
  ipcMain.handle('desktop:startup-status', async (event) => {
    if (!fromSplash(event)) return null
    const status = await readStatus()
    // A hidden start must not hide a failure: nobody would ever learn why Orin is not running.
    if (status && status.mode === 'error' && startHidden) showWindow()
    return status
  })
  ipcMain.handle('desktop:get-preferences', (event) => fromApp(event) ? { ...preferences } : null)
  ipcMain.handle('desktop:set-preferences', async (event, value) => {
    if (!fromApp(event)) return null
    const behavior = value && value.closeBehavior
    if (CLOSE_BEHAVIORS.includes(behavior)) await savePreferences({ ...preferences, closeBehavior: behavior })
    return { ...preferences }
  })
  ipcMain.handle('desktop:load-app', async (event, url) => {
    if (!fromSplash(event)) return false
    if (!isLocalOrinUrl(url)) return false
    appUrl = url
    await mainWindow.loadURL(url)
    watchLauncherLifecycle()
    checkForUpdate()
    return true
  })
  ipcMain.handle('desktop:open-logs', async (event) => {
    if (!fromSplash(event)) return ''
    const status = await readStatus()
    if (!status || typeof status.logs_dir !== 'string') return ''
    return shell.openPath(status.logs_dir)
  })
  ipcMain.handle('desktop:retry', (event) => fromSplash(event) ? retryStartup() : false)
  ipcMain.handle('desktop:apply-update', (event) => fromApp(event) ? applyUpdate() : false)
  ipcMain.handle('desktop:close', (event) => {
    if (!fromSplash(event)) return false
    closeWindow()
    return true
  })
  ipcMain.handle('desktop:notify-code-mode', (event, payload) => {
    if (!fromApp(event) || !Notification.isSupported() || !safeCodeModeNotification(payload)) return false
    const notification = new Notification({ title: payload.title, body: payload.body, icon: iconPath, silent: false })
    notification.on('click', () => showWindow())
    notification.show()
    return true
  })
}

function safeCodeModeNotification(value) {
  if (!value || typeof value !== 'object') return false
  if (typeof value.title !== 'string' || typeof value.body !== 'string') return false
  // Notification text must remain a short status, never a path, log, token or diff.
  return value.title.length > 0 && value.title.length <= 80 && value.body.length > 0 && value.body.length <= 180
}

async function checkForUpdate() {
  const cache = path.join(app.getPath('userData'), 'orin-update.json')
  let prior = null
  try {
    prior = JSON.parse(await fs.readFile(cache, 'utf8'))
    if (prior.checkedAt && Date.now() - Date.parse(prior.checkedAt) < 24 * 60 * 60 * 1000) {
      const cachedRelease = updateRelease(prior.release)
      if (cachedRelease && isNewerVersion(cachedRelease.version, app.getVersion())) showUpdateFlag(cachedRelease)
      return
    }
  } catch {}
  try {
    const response = await fetch('https://github.com/carlos-edu2367/orin/releases/latest/download/release.json', { signal: AbortSignal.timeout(3500) })
    if (!response.ok) return
    const release = updateRelease(await response.json())
    await fs.writeFile(cache, JSON.stringify({ checkedAt: new Date().toISOString(), release }), 'utf8')
    if (!release || !isNewerVersion(release.version, app.getVersion())) return
    showUpdateFlag(release)
  } catch {
    // Updates are advisory. Offline/startup use must never be delayed or fail.
  }
}

function updateRelease(value) {
  if (!value || typeof value.version !== 'string') return null
  return { version: value.version, releaseUrl: repositoryReleaseUrl(value.release_url) }
}

function showUpdateFlag(release) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  const currentVersion = app.getVersion()
  const label = `Orin ${release.version} is available. Open Orin to download and install it.`
  mainWindow.setTitle(`Orin - Update ${release.version} available`)
  if (process.platform === 'win32') mainWindow.setOverlayIcon(updateOverlayIcon(), label)
  mainWindow.webContents.send('desktop:update-available', {
    currentVersion,
    latestVersion: release.version,
  })
}

// The app downloads and verifies the release in the background (the API's
// update job); this only performs the last step. `orin update --apply --restart`
// stops this Orin, activates the prepared version (undoing it automatically if
// the new one does not start) and reopens the window. It must outlive us, hence
// detached: this window closes itself when the launcher reports "stopped".
async function applyUpdate() {
  if (updating) return false
  const command = await updateCommand()
  if (!command) return false
  try {
    const child = spawn(command[0], command.slice(1), {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    })
    child.unref()
    updating = true
    return true
  } catch {
    return false
  }
}

async function updateCommand() {
  const status = await readStatus()
  if (!status || !Array.isArray(status.restart_command) || status.restart_command.length === 0) return null
  if (!status.restart_command.every((part) => typeof part === 'string' && part.length > 0)) return null
  const [executable, ...arguments] = status.restart_command
  try {
    await fs.access(executable)
  } catch {
    return null
  }
  return [executable, ...arguments, 'update', '--apply', '--restart']
}

function updateOverlayIcon() {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><circle cx="16" cy="16" r="15" fill="#a855f7"/><path d="M16 7v12m0 0-5-5m5 5 5-5M9 24h14" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>'
  return nativeImage.createFromDataURL(`data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`)
}

function repositoryReleaseUrl(value) {
  const fallback = 'https://github.com/carlos-edu2367/orin/releases/latest'
  return typeof value === 'string' && value.startsWith('https://github.com/carlos-edu2367/orin/releases/') ? value : fallback
}

function isNewerVersion(candidate, current) {
  const parse = (value) => value.replace(/^v/, '').split('.').map((part) => Number.parseInt(part, 10) || 0)
  const [a, b, c] = parse(candidate)
  const [x, y, z] = parse(current)
  return a > x || (a === x && (b > y || (b === y && c > z)))
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
  destroyTray()
  await requestShutdown()
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy()
  app.quit()
}

// Closing the window of a running Orin is a real choice: quitting also stops the
// scheduler and the worker, so scheduled work stops with it.
async function handleCloseRequest() {
  // Still starting (or failed): there is nothing to keep alive.
  if (!appUrl || updating) return closeWindow()
  if (preferences.closeBehavior === 'quit') return closeWindow()
  if (preferences.closeBehavior === 'background') return hideToBackground()
  if (asking) return
  asking = true
  try {
    const { response, checkboxChecked } = await dialog.showMessageBox(mainWindow, {
      type: 'question',
      title: 'Fechar o Orin?',
      message: 'Fechar o Orin ou mantê-lo em segundo plano?',
      detail: 'Em segundo plano o Orin continua rodando, sem janela, e suas tarefas agendadas seguem funcionando. Para abri-lo de novo, use o ícone na bandeja do sistema.\n\nVocê pode mudar isso depois em Configurações.',
      buttons: ['Manter em segundo plano', 'Fechar o Orin', 'Cancelar'],
      defaultId: 0,
      cancelId: 2,
      noLink: true,
      checkboxLabel: 'Lembrar minha escolha',
      checkboxChecked: false,
      icon: nativeImage.createFromPath(iconPath),
    })
    if (response === 2) return
    if (checkboxChecked) await savePreferences({ ...preferences, closeBehavior: response === 0 ? 'background' : 'quit' })
    if (response === 0) hideToBackground()
    else closeWindow()
  } finally {
    asking = false
  }
}

function hideToBackground() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (!createTray()) {
    // No tray on this desktop: minimizing keeps a visible way back.
    mainWindow.minimize()
    return
  }
  mainWindow.hide()
  if (!announcedBackground && Notification.isSupported()) {
    announcedBackground = true
    new Notification({ title: 'O Orin continua em segundo plano', body: 'Suas tarefas agendadas seguem rodando. Use o ícone da bandeja para abrir ou sair.', icon: iconPath, silent: true }).show()
  }
}

function showWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  destroyTray()
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

function createTray() {
  if (tray) return true
  try {
    const image = nativeImage.createFromPath(iconPath).resize({ width: 18, height: 18 })
    tray = new Tray(image)
    tray.setToolTip('Orin — em segundo plano')
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Abrir o Orin', click: () => showWindow() },
      { type: 'separator' },
      { label: 'Sair do Orin', click: () => closeWindow() },
    ]))
    tray.on('click', () => showWindow())
    return true
  } catch {
    tray = null
    return false
  }
}

function destroyTray() {
  if (!tray) return
  tray.destroy()
  tray = null
}

function preferencesFile() {
  return path.join(app.getPath('userData'), 'orin-preferences.json')
}

async function loadPreferences() {
  try {
    const stored = JSON.parse(await fs.readFile(preferencesFile(), 'utf8'))
    if (CLOSE_BEHAVIORS.includes(stored.closeBehavior)) return { closeBehavior: stored.closeBehavior }
  } catch {}
  return { closeBehavior: 'ask' }
}

async function savePreferences(next) {
  preferences = next
  try {
    await fs.writeFile(preferencesFile(), JSON.stringify(next), 'utf8')
  } catch {
    // Not persisting only means asking again next time.
  }
}

function watchLauncherLifecycle() {
  if (lifecycleTimer) return
  lifecycleTimer = setInterval(async () => {
    const status = await readStatus()
    if (!status || status.mode !== 'stopped' || closing) return
    closing = true
    destroyTray()
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

function fromApp(event) {
  return isLocalOrinUrl(event.senderFrame.url)
}

function argumentValue(name) {
  const index = process.argv.indexOf(name)
  return index >= 0 ? process.argv[index + 1] : null
}
