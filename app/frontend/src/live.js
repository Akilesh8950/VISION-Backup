// Vision Live microphone + capability helpers.
//
// Push-to-talk only: the recorder exists solely between an explicit press
// and release. The clip is decoded/resampled to the exact 16 kHz mono
// PCM16 WAV shape whisper.cpp expects, held only in memory, and sent to the
// local Python bridge for on-device transcription. It is never uploaded,
// written to browser storage, or kept after the turn.

export const LIVE_MAX_RECORDING_MS = 60_000

export const STT_LANGUAGE_OPTIONS = [
  ['auto', 'Automatic'],
  ['en', 'English'],
  ['ta', 'Tamil'],
  ['hi', 'Hindi'],
  ['te', 'Telugu'],
  ['ml', 'Malayalam'],
  ['kn', 'Kannada'],
]

export function normalizeLiveCapabilities(raw = {}, native = false) {
  const src = raw && typeof raw === 'object' ? raw : {}
  const stt = src.speech_to_text && typeof src.speech_to_text === 'object' ? src.speech_to_text : {}
  const vision = src.screen_vision && typeof src.screen_vision === 'object' ? src.screen_vision : {}
  return {
    voice_chat_available: Boolean(src.voice_chat_available),
    speech_to_text: {
      engine: stt.engine || 'whisper.cpp',
      available: Boolean(stt.available),
      binary_downloaded: Boolean(stt.binary_downloaded),
      binary_name: String(stt.binary_name || ''),
      model_downloaded: Boolean(stt.model_downloaded),
      model_size: ['tiny', 'base', 'small'].includes(stt.model_size) ? stt.model_size : 'base',
      model_label: String(stt.model_label || 'Base — balanced (148 MB)'),
      model_bytes: Number(stt.model_bytes || 147_951_465),
      language: String(stt.language || 'auto'),
      platform_supported: Boolean(stt.platform_supported),
      reason: String(stt.reason || (native ? 'Download the local speech-to-text engine and voice model.' : 'Vision Live runs in the Windows desktop app.')),
      models: Array.isArray(stt.models) && stt.models.length ? stt.models : [
        { size: 'tiny', label: 'Tiny — fastest (78 MB)', bytes: 77_691_713 },
        { size: 'base', label: 'Base — balanced (148 MB)', bytes: 147_951_465 },
        { size: 'small', label: 'Small — most accurate (488 MB)', bytes: 487_601_967 },
      ],
    },
    screen_capture_available: Boolean(src.screen_capture_available),
    screen_vision: {
      available: Boolean(vision.available),
      configured: Boolean(vision.configured),
      mmproj_name: String(vision.mmproj_name || ''),
      reason: String(vision.reason || 'The installed VISION model is text-only (no matching mmproj vision file), so screenshots cannot be understood yet.'),
    },
    mini_open: Boolean(src.mini_open),
    privacy: {
      push_to_talk_only: true,
      audio_saved: false,
      screen_saved: false,
      screen_remote_allowed: false,
      computer_control: false,
    },
  }
}

export function micSupported() {
  try {
    return Boolean(navigator.mediaDevices?.getUserMedia && typeof window.MediaRecorder === 'function')
  } catch {
    return false
  }
}

export function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return window.btoa(binary)
}

export function encodeWavPcm16(samples, sampleRate = 16_000) {
  const dataLength = samples.length * 2
  const buffer = new ArrayBuffer(44 + dataLength)
  const view = new DataView(buffer)
  const writeText = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)) }
  writeText(0, 'RIFF')
  view.setUint32(4, 36 + dataLength, true)
  writeText(8, 'WAVE')
  writeText(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeText(36, 'data')
  view.setUint32(40, dataLength, true)
  let offset = 44
  for (let i = 0; i < samples.length; i += 1, offset += 2) {
    const sample = Math.max(-1, Math.min(1, samples[i] || 0))
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
  }
  return buffer
}

async function blobTo16kWavBase64(blob) {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext
  if (!AudioContextCtor) throw new Error('This window cannot prepare microphone audio.')
  const decodeContext = new AudioContextCtor()
  let decoded
  try {
    decoded = await decodeContext.decodeAudioData(await blob.arrayBuffer())
  } finally {
    try { await decodeContext.close() } catch { /* already closed */ }
  }
  const frameCount = Math.max(1, Math.ceil(decoded.duration * 16_000))
  const offline = new OfflineAudioContext(1, frameCount, 16_000)
  const source = offline.createBufferSource()
  source.buffer = decoded
  source.connect(offline.destination)
  source.start()
  const rendered = await offline.startRendering()
  return arrayBufferToBase64(encodeWavPcm16(rendered.getChannelData(0), 16_000))
}

function preferredMimeType() {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus']
  return candidates.find((type) => {
    try { return window.MediaRecorder.isTypeSupported(type) } catch { return false }
  }) || ''
}

export class PushToTalkRecorder {
  constructor({ onLevel = null, onAutoStop = null } = {}) {
    this.onLevel = onLevel
    this.onAutoStop = onAutoStop
    this.stream = null
    this.recorder = null
    this.chunks = []
    this.audioContext = null
    this.analyser = null
    this.frame = 0
    this.timer = 0
    this.active = false
    this.discarded = false
    this.lastLevelAt = 0
  }

  async start() {
    if (this.active) return
    if (!micSupported()) throw new Error('Microphone recording is not available in this window.')
    this.discarded = false
    this.chunks = []
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false,
    })
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext
    if (AudioContextCtor) {
      this.audioContext = new AudioContextCtor()
      const source = this.audioContext.createMediaStreamSource(this.stream)
      this.analyser = this.audioContext.createAnalyser()
      this.analyser.fftSize = 512
      source.connect(this.analyser)
      const data = new Uint8Array(this.analyser.fftSize)
      const tick = () => {
        if (!this.active || !this.analyser) return
        this.analyser.getByteTimeDomainData(data)
        let sum = 0
        for (const value of data) { const centred = (value - 128) / 128; sum += centred * centred }
        const rms = Math.min(1, Math.sqrt(sum / data.length) * 3.2)
        const now = performance.now()
        if (now - this.lastLevelAt > 70) { this.lastLevelAt = now; this.onLevel?.(rms) }
        this.frame = window.requestAnimationFrame(tick)
      }
      this.frame = window.requestAnimationFrame(tick)
    }
    const mimeType = preferredMimeType()
    this.recorder = new window.MediaRecorder(this.stream, mimeType ? { mimeType } : undefined)
    this.recorder.ondataavailable = (event) => { if (event.data?.size) this.chunks.push(event.data) }
    this.active = true
    this.recorder.start(250)
    this.timer = window.setTimeout(() => this.onAutoStop?.(), LIVE_MAX_RECORDING_MS)
  }

  async stop() {
    if (!this.active || !this.recorder) { this._cleanup(); return null }
    const recorder = this.recorder
    const stopped = new Promise((resolve) => { recorder.onstop = resolve })
    try { recorder.stop() } catch { /* onstop may not fire after an engine error */ }
    await Promise.race([stopped, new Promise((resolve) => window.setTimeout(resolve, 1200))])
    const discarded = this.discarded
    const chunks = this.chunks
    const type = recorder.mimeType || chunks[0]?.type || 'audio/webm'
    this._cleanup()
    if (discarded || !chunks.length) return null
    return blobTo16kWavBase64(new Blob(chunks, { type }))
  }

  cancel() {
    this.discarded = true
    if (this.recorder && this.recorder.state !== 'inactive') {
      try { this.recorder.stop() } catch { /* stream cleanup below is authoritative */ }
    }
    this._cleanup()
  }

  _cleanup() {
    this.active = false
    if (this.timer) window.clearTimeout(this.timer)
    if (this.frame) window.cancelAnimationFrame(this.frame)
    this.timer = 0
    this.frame = 0
    try { this.stream?.getTracks().forEach((track) => track.stop()) } catch { /* tracks already ended */ }
    this.stream = null
    this.recorder = null
    this.chunks = []
    this.analyser = null
    if (this.audioContext) {
      const context = this.audioContext
      this.audioContext = null
      context.close().catch(() => {})
    }
    this.onLevel?.(0)
  }
}

export function trimCaption(text, limit = 700) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim()
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean
}
