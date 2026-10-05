/// <reference types="vite/client" />

declare module '*.css'

interface OrinDesktopBridge {
  onUpdateAvailable: (callback: (update: { currentVersion: string; latestVersion: string }) => void) => () => void
  /** Activates the downloaded update and reopens Orin on the new version. */
  applyUpdate?: () => Promise<boolean>
  notifyCodeMode?: (notification: { title: string; body: string }) => Promise<boolean>
}

interface Window {
  orinDesktop?: OrinDesktopBridge
}
