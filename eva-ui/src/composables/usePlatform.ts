export function usePlatform() {
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
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title, { body })
    }
  }

  return { hasWebGL, particleBudget, enable3D, sendNotification }
}
