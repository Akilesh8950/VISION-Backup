const listeners = new Map();
const queuedEvents = [];
let nativeReady = false;
let readyPromise = null;

function dispatch(type, payload) {
  const callbacks = listeners.get(type) || [];
  const wildcard = listeners.get("*") || [];
  for (const callback of [...callbacks, ...wildcard]) {
    try {
      callback(payload, type);
    } catch (error) {
      console.error(`VISION event handler failed for ${type}`, error);
    }
  }
}

function flushQueuedEvents() {
  while (queuedEvents.length) {
    const event = queuedEvents.shift();
    dispatch(event.type, event.payload || {});
  }
}

window.VisionBridge = {
  receive(event) {
    if (!event || typeof event.type !== "string") return;
    if (!listeners.size) {
      queuedEvents.push(event);
      return;
    }
    dispatch(event.type, event.payload || {});
  },
};

export function on(type, callback) {
  if (!listeners.has(type)) listeners.set(type, []);
  listeners.get(type).push(callback);
  flushQueuedEvents();
  return () => {
    const callbacks = listeners.get(type) || [];
    const index = callbacks.indexOf(callback);
    if (index >= 0) callbacks.splice(index, 1);
  };
}

export function isNative() {
  return Boolean(window.pywebview?.api);
}

export function waitForNative(timeoutMs = 3500) {
  if (isNative()) {
    nativeReady = true;
    return Promise.resolve(true);
  }
  if (readyPromise) return readyPromise;
  readyPromise = new Promise((resolve) => {
    const started = performance.now();
    const timer = window.setInterval(() => {
      if (isNative()) {
        nativeReady = true;
        window.clearInterval(timer);
        resolve(true);
      } else if (performance.now() - started > timeoutMs) {
        window.clearInterval(timer);
        resolve(false);
      }
    }, 40);
  });
  return readyPromise;
}

window.addEventListener("pywebviewready", () => {
  nativeReady = true;
});

export async function call(method, ...args) {
  const available = await waitForNative();
  if (!available || !window.pywebview?.api?.[method]) {
    throw new Error(`VISION native bridge is unavailable (${method})`);
  }
  return window.pywebview.api[method](...args);
}

export async function callOptional(method, fallback, ...args) {
  try {
    return await call(method, ...args);
  } catch (_error) {
    return fallback;
  }
}

export function nativeIsReady() {
  return nativeReady || isNative();
}
