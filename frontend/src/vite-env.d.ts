/// <reference types="vite/client" />

declare module '*.css'

interface OrinDesktopBridge {
  onUpdateAvailable: (callback: (update: { currentVersion: string; latestVersion: string }) => void) => () => void
  /** Activates the downloaded update and reopens Orin on the new version. */
  applyUpdate?: () => Promise<boolean>
  /** What closing the window does: ask, keep running in the tray, or quit. */
  getPreferences?: () => Promise<{ closeBehavior: 'ask' | 'background' | 'quit' } | null>
  setPreferences?: (preferences: { closeBehavior: 'ask' | 'background' | 'quit' }) => Promise<{ closeBehavior: 'ask' | 'background' | 'quit' } | null>
  notifyCodeMode?: (notification: { title: string; body: string }) => Promise<boolean>
}

interface Window {
  orinDesktop?: OrinDesktopBridge
}
