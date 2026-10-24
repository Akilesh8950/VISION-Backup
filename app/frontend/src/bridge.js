const listeners = new Map()
const early = []
let readyPromise = null

function dispatch(type, payload) {
  for (const cb of [...(listeners.get(type) || []), ...(listeners.get('*') || [])]) {
    try { cb(payload || {}, type) } catch (error) { console.error(`VISION event handler failed: ${type}`, error) }
  }
}

if (typeof window !== 'undefined') {
  window.VisionBridge = {
    receive(event) {
      if (!event || typeof event.type !== 'string') return
      if (!listeners.size) { early.push(event); return }
      dispatch(event.type, event.payload || {})
    },
  }
}

export function on(type, callback) {
  if (!listeners.has(type)) listeners.set(type, [])
  listeners.get(type).push(callback)
  while (early.length) {
    const event = early.shift()
    dispatch(event.type, event.payload || {})
  }
  return () => {
    const list = listeners.get(type) || []
    const index = list.indexOf(callback)
    if (index >= 0) list.splice(index, 1)
  }
}

export function isNative() {
  return Boolean(window.pywebview?.api)
}

export function waitForNative(timeoutMs = 3000) {
  if (isNative()) return Promise.resolve(true)
  if (readyPromise) return readyPromise
  readyPromise = new Promise((resolve) => {
    const started = performance.now()
    const timer = window.setInterval(() => {
      if (isNative()) { window.clearInterval(timer); resolve(true) }
      else if (performance.now() - started > timeoutMs) { window.clearInterval(timer); resolve(false) }
    }, 40)
  })
  return readyPromise
}

export async function call(method, ...args) {
  const available = await waitForNative()
  if (!available || !window.pywebview?.api?.[method]) {
    throw new Error(`VISION native bridge is unavailable (${method})`)
  }
  return window.pywebview.api[method](...args)
}

export async function callOptional(method, fallback, ...args) {
  try { return await call(method, ...args) } catch { return fallback }
}

export async function api(method, ...args) {
  // Kept intentionally quiet: App.jsx decides how to surface failures.
  try { return await call(method, ...args) } catch { return null }
}
