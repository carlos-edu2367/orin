/**
 * System notifications (W7): the desktop app's own, or — in a browser — the Web Notifications API. Nothing is shown
 * unless the person turned notifications on (`system_notifications`) and, in a browser, allowed them; this module
 * never asks for permission by itself (only `requestSystemNotifications`, called from that setting, does).
 */

import { playNotifySound, type NotifySoundKind } from './notifySound'

export type SystemNotice = {
  title: string
  body: string
  kind?: NotifySoundKind
  sound?: boolean
  silent?: boolean
}

const TITLE_MAX = 80
const BODY_MAX = 180

function clip(value: string, max: number): string {
  const clean = value.replace(/\s+/g, ' ').trim()
  return clean.length > max ? `${clean.slice(0, max - 1)}…` : clean
}

function browserNotifications(): typeof Notification | null {
  return typeof window !== 'undefined' && 'Notification' in window ? window.Notification : null
}

/** Shows a notification; `false` when there is no way to (no desktop bridge and no permission in the browser). */
export function notifySystem(notice: SystemNotice): boolean {
  const title = clip(notice.title, TITLE_MAX)
  const body = clip(notice.body, BODY_MAX)
  if (!title || !body) return false

  const shouldPlayOrinSound = notice.sound === true || (notice.sound === undefined && notice.kind !== undefined)
  const explicitSilent =
    notice.silent !== undefined
      ? notice.silent
      : shouldPlayOrinSound
        ? true
        : notice.sound === false
          ? false
          : undefined

  if (window.orinDesktop?.notifyCodeMode) {
    const payload = explicitSilent !== undefined ? { title, body, silent: explicitSilent } : { title, body }
    void window.orinDesktop.notifyCodeMode(payload)
    if (shouldPlayOrinSound) {
      playNotifySound(notice.kind ?? 'attention', true)
    }
    return true
  }
  const Api = browserNotifications()
  if (!Api || Api.permission !== 'granted') return false
  try {
    const notification = new Api(title, {
      body,
      tag: 'orin',
      ...(explicitSilent !== undefined ? { silent: explicitSilent } : {}),
    })
    notification.onclick = () => { window.focus(); notification.close() }
    if (shouldPlayOrinSound) {
      playNotifySound(notice.kind ?? 'attention', true)
    }
    return true
  } catch {
    return false // some browsers allow notifications only from a service worker
  }
}

/** Asks the browser for permission (after the person turned notifications on); the desktop app needs none. */
export async function requestSystemNotifications(): Promise<'granted' | 'denied' | 'default' | 'unsupported'> {
  if (window.orinDesktop?.notifyCodeMode) return 'granted'
  const Api = browserNotifications()
  if (!Api) return 'unsupported'
  if (Api.permission !== 'default') return Api.permission
  try {
    return await Api.requestPermission()
  } catch {
    return 'denied'
  }
}

/** A window the person is not looking at: a hidden tab, minimized window, or an unfocused window. */
export function windowIsAway(): boolean {
  if (typeof document === 'undefined') return false
  if (document.visibilityState !== 'visible') return true
  if (typeof document.hasFocus === 'function' && !document.hasFocus()) return true
  return false
}
