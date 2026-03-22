export function usePlatform() {
  const isTauri = '__TAURI__' in window

  const hasWebGL = (() => {
    try {
      const c = document.createElement('canvas')
      return !!(c.getContext('webgl2') || c.getContext('webgl'))
    } catch { return false }
  })()

  const particleBudget = hasWebGL
    ? (navigator.hardwareConcurrency >= 4 ? 2000 : 500)
    : 0

  const enable3D = hasWebGL && particleBudget > 0

  async function sendNotification(title: string, body: string) {
    if (isTauri) {
      try {
        // Dynamic import — @tauri-apps/api may not be installed in web-only builds
        const tauriMod = '@tauri-apps/api/notification'
        const mod: any = await (Function('m', 'return import(m)') as (m: string) => Promise<any>)(tauriMod)
        mod.sendNotification({ title, body })
      } catch { /* Tauri API not available */ }
    } else if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title, { body })
    }
  }

  return { isTauri, hasWebGL, particleBudget, enable3D, sendNotification }
}
