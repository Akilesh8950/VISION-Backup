const listeners = new Map()
const earlyByType = new Map()
const MAX_EARLY_PER_TYPE = 250
let readyPromise = null

function handlersFor(type) {
  return [...(listeners.get(type) || []), ...(type === '*' ? [] : listeners.get('*') || [])]
}

function dispatch(type, payload) {
  for (const cb of handlersFor(type)) {
    try { cb(payload || {}, type) } catch (error) { console.error(`VISION event handler failed: ${type}`, error) }
  }
}

function queueEarly(event) {
  const queue = earlyByType.get(event.type) || []
  queue.push(event)
  if (queue.length > MAX_EARLY_PER_TYPE) queue.splice(0, queue.length - MAX_EARLY_PER_TYPE)
  earlyByType.set(event.type, queue)
}

if (typeof window !== 'undefined') {
  window.VisionBridge = {
    receive(event) {
      if (!event || typeof event.type !== 'string') return
      // Preserve events whose specific listener has not mounted yet. The old
      // implementation flushed every queued event when *any* type was first
      // registered, silently discarding all other event types.
      if (handlersFor(event.type).length === 0) queueEarly(event)
      else dispatch(event.type, event.payload || {})
    },
  }
}

export function on(type, callback) {
  if (!listeners.has(type)) listeners.set(type, [])
  listeners.get(type).push(callback)

  if (type === '*') {
    for (const [queuedType, queue] of [...earlyByType.entries()]) {
      earlyByType.delete(queuedType)
      for (const event of queue) dispatch(queuedType, event.payload || {})
    }
  } else {
    const queue = earlyByType.get(type) || []
    earlyByType.delete(type)
    for (const event of queue) dispatch(type, event.payload || {})
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
