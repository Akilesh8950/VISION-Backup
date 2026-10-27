// Read-aloud (text-to-speech) engine for VISION.
//
// Design notes:
// - Uses the Web Speech API (`speechSynthesis`), which in the packaged app
//   is provided by WebView2/Chromium from the speech voices installed in
//   Windows. Fully offline for local voices; nothing leaves the PC.
// - There is no single cross-PC "soothing voice": installed voices differ per
//   machine. The soothing experience therefore comes from (a) ranking the
//   installed voices so the most natural, calm one wins by default, and
//   (b) style presets that slow the rate slightly and soften the pitch.
// - Long single utterances are silently truncated by Chromium, so answers
//   are split into sentence-sized chunks and queued.
// - Voice lists arrive asynchronously (`voiceschanged`); every entry point
//   tolerates an empty list and degrades to a clear "unavailable" status.

export const SAMPLE_TEXT = "Hello, I'm VISION, built by SPIRIT ORG. Take a slow breath, and take your time. Ask me anything at all, and whenever you like, I'll read the answer aloud for you, calmly and clearly."

export const VOICE_STYLES = {
  soothing: { label: 'Soothing — warm & slow (recommended)', rate: 0.92, pitch: 0.95, volume: 1, gender: 'female' },
  calm_deep: { label: 'Calm & deep', rate: 0.88, pitch: 0.82, volume: 1, gender: 'male' },
  natural: { label: 'Natural', rate: 1, pitch: 1, volume: 1, gender: 'any' },
  bright: { label: 'Bright & clear', rate: 1.02, pitch: 1.12, volume: 1, gender: 'any' },
  slow_clear: { label: 'Slow & clear', rate: 0.78, pitch: 1, volume: 1, gender: 'any' },
  custom: { label: 'Custom (your sliders below)', rate: null, pitch: null, volume: null, gender: 'any' },
}

export const DEFAULT_TTS = {
  tts_voice_uri: '',
  tts_voice_name: '',
  tts_style: 'soothing',
  tts_rate: VOICE_STYLES.soothing.rate,
  tts_pitch: VOICE_STYLES.soothing.pitch,
  tts_volume: 1,
  tts_auto_read: false,
}

const clamp = (value, lo, hi, fallback) => {
  const n = Number(value)
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback
}

// Mirror of the backend validators in src/vision_app/config.py: clamp, never
// throw, so a stale or foreign value can never break read-aloud.
export function normalizeTts(raw = {}) {
  const src = raw && typeof raw === 'object' ? raw : {}
  const style = Object.prototype.hasOwnProperty.call(VOICE_STYLES, src.tts_style) ? src.tts_style : 'soothing'
  return {
    tts_voice_uri: String(src.tts_voice_uri || '').slice(0, 300),
    tts_voice_name: String(src.tts_voice_name || '').slice(0, 300),
    tts_style: style,
    tts_rate: clamp(src.tts_rate, 0.5, 1.5, DEFAULT_TTS.tts_rate),
    tts_pitch: clamp(src.tts_pitch, 0.5, 1.5, DEFAULT_TTS.tts_pitch),
    tts_volume: clamp(src.tts_volume, 0, 1, 1),
    tts_auto_read: Boolean(src.tts_auto_read),
  }
}

export function styleGender(style) {
  return VOICE_STYLES[style]?.gender || 'any'
}

export function speechAvailable() {
  try {
    return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'
  } catch {
    return false
  }
}

// Common Windows / Chromium voice first-names, used only as a soft ranking
// hint for the "warm female" / "calm male" styles. Unknown voices are never
// excluded — an explicit pick in Settings always wins.
const FEMALE_NAMES = ['zira', 'aria', 'jenny', 'michelle', 'ana', 'emma', 'ava', 'sara', 'samantha', 'victoria', 'karen', 'moira', 'tessa', 'fiona', 'veena', 'heera', 'priya', 'neha', 'swara', 'amber', 'ashley', 'cora', 'elizabeth', 'catherine', 'hayley', 'natasha', 'clara', 'heather', 'susan', 'hazel', 'linda']
const MALE_NAMES = ['david', 'christopher', 'eric', 'guy', 'roger', 'steffan', 'brian', 'ryan', 'daniel', 'alex', 'fred', 'prabhat', 'arjun', 'william', 'liam', 'mitchell', 'james', 'george', 'thomas', 'davis', 'brandon', 'jacob', 'andrew']
const NOVELTY = ['grandpa', 'grandma', 'bad news', 'good news', 'bells', 'boing', 'bubbles', 'cellos', 'deranged', 'trinoids', 'zarvox', 'hysterical', 'bahh', 'junior', 'princess', 'superstar', 'whisper']

export function voiceGender(voice) {
  const name = String(voice?.name || '').toLowerCase()
  if (!name) return 'unknown'
  if (name.includes('female')) return 'female'
  if (FEMALE_NAMES.some((n) => name.includes(n))) return 'female'
  if (name.includes(' male') || name.endsWith('male')) return 'male'
  if (MALE_NAMES.some((n) => name.includes(n))) return 'male'
  return 'unknown'
}

function scoreVoice(voice, genderPref, wantLang) {
  const name = String(voice?.name || '').toLowerCase()
  const lang = String(voice?.lang || '').toLowerCase()
  let score = 0
  // Natural/neural voices are dramatically calmer than legacy SAPI voices.
  if (/natural|neural/.test(name)) score += 60
  // Local voices keep working offline; online "Natural" voices need network.
  if (voice?.localService) score += 25
  else if (/online/.test(name)) score += 10
  const want = String(wantLang || '').toLowerCase()
  if (want && lang === want) score += 40
  else if (want && lang && lang.split('-')[0] === want.split('-')[0]) score += 25
  else if (lang.startsWith('en')) score += 10
  if (genderPref && genderPref !== 'any') {
    const g = voiceGender(voice)
    if (g === genderPref) score += 50
    else if (g !== 'unknown') score -= 20
  }
  if (voice?.default) score += 5
  if (NOVELTY.some((n) => name.includes(n))) score -= 80
  return score
}

export function rankVoices(voices = [], genderPref = 'any', lang = '') {
  const want = lang || (typeof navigator !== 'undefined' ? navigator.language : '')
  return [...voices]
    .map((voice, index) => ({ voice, index, score: scoreVoice(voice, genderPref, want) }))
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((row) => row.voice)
}

export function pickVoice(voices = [], prefs = DEFAULT_TTS) {
  if (!voices.length) return null
  const clean = normalizeTts(prefs)
  if (clean.tts_voice_uri) {
    const byUri = voices.find((v) => v.voiceURI === clean.tts_voice_uri)
    if (byUri) return byUri
  }
  if (clean.tts_voice_name) {
    const byName = voices.find((v) => v.name === clean.tts_voice_name)
    if (byName) return byName
  }
  return rankVoices(voices, styleGender(clean.tts_style))[0] || null
}

export function voiceOptionLabel(voice) {
  const bits = [String(voice?.name || 'Voice').replace(/\s*-\s*[^-]+$/, '').trim() || String(voice?.name || 'Voice')]
  if (voice?.lang) bits.push(voice.lang)
  if (/natural|neural/i.test(voice?.name || '')) bits.push('Natural')
  if (voice && voice.localService === false) bits.push('Online')
  return bits.join(' · ')
}

export function listVoices() {
  if (!speechAvailable()) return []
  try {
    return window.speechSynthesis.getVoices() || []
  } catch {
    return []
  }
}

// Voices often load after first paint. Resolve with whatever is available
// now, or as soon as `voiceschanged` fires (bounded wait).
export function loadVoices(timeoutMs = 1200) {
  const now = listVoices()
  if (now.length || !speechAvailable()) return Promise.resolve(now)
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      try { window.speechSynthesis.removeEventListener('voiceschanged', finish) } catch { /* older engines */ }
      resolve(listVoices())
    }
    try { window.speechSynthesis.addEventListener('voiceschanged', finish) } catch { /* bounded by timeout */ }
    window.setTimeout(finish, timeoutMs)
  })
}

export function plainSpeechText(markdown) {
  let text = String(markdown || '')
  text = text
    .replace(/```[\s\S]*?(```|$)/g, ' Code block omitted. ')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, (_, alt) => (alt ? ` Image: ${alt}. ` : ' Image. '))
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' link ')
    .replace(/\[S\d+\]/gi, ' ')
    .replace(/`([^`]+)`/g, '$1')
  // Tables: read cell text row by row, drop the --- separator row.
  text = text.split('\n').map((line) => {
    const trimmed = line.trim()
    if (!trimmed.includes('|')) return line
    if (/^\|?[\s:|-]+\|?$/.test(trimmed)) return ''
    return trimmed.replace(/^\||\|$/g, '').split('|').map((c) => c.trim()).filter(Boolean).join(', ')
  }).join('. ')
  text = text
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^>\s?/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .replace(/~~(.*?)~~/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  return text.slice(0, 8000)
}

export function chunkForSpeech(text, max = 220) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim()
  if (!clean) return []
  const sentences = clean.match(/[^.!?…]+[.!?…]+["'”)]*|\S[^.!?…]*$/g) || [clean]
  const chunks = []
  let current = ''
  const pushWords = (long) => {
    let rest = long.trim()
    while (rest.length > max) {
      let cut = rest.lastIndexOf(' ', max)
      if (cut < 60) cut = max
      chunks.push(rest.slice(0, cut).trim())
      rest = rest.slice(cut).trim()
    }
    if (rest) chunks.push(rest)
  }
  for (const raw of sentences) {
    const sentence = raw.trim()
    if (!sentence) continue
    if (sentence.length > max) {
      if (current) { chunks.push(current); current = '' }
      pushWords(sentence)
      continue
    }
    if (current && current.length + 1 + sentence.length > max) {
      chunks.push(current)
      current = sentence
    } else {
      current = current ? `${current} ${sentence}` : sentence
    }
  }
  if (current) chunks.push(current)
  return chunks.slice(0, 60)
}

class VoiceEngine {
  constructor() {
    this.speakingKey = null
    this.listeners = new Set()
    this.session = 0
  }

  subscribe(fn) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  _emit() {
    for (const fn of this.listeners) {
      try { fn(this.speakingKey) } catch { /* listener errors must not break speech */ }
    }
  }

  _setKey(key) {
    if (this.speakingKey === key) return
    this.speakingKey = key
    this._emit()
  }

  stop() {
    this.session += 1
    try { window.speechSynthesis?.cancel() } catch { /* engine already gone */ }
    this._setKey(null)
  }

  // Returns 'started' | 'empty' | 'unavailable' | 'no-voices'.
  async speak(rawText, prefs = DEFAULT_TTS, key = '') {
    if (!speechAvailable()) return 'unavailable'
    const clean = normalizeTts(prefs)
    const chunks = chunkForSpeech(plainSpeechText(rawText))
    if (!chunks.length) return 'empty'
    const voices = await loadVoices()
    if (!voices.length) return 'no-voices'
    const voice = pickVoice(voices, clean)
    this.stop()
    const session = this.session
    let remaining = chunks.length
    const done = () => {
      remaining -= 1
      if (remaining <= 0 && this.session === session) this._setKey(null)
    }
    try {
      chunks.forEach((chunk, index) => {
        const utter = new window.SpeechSynthesisUtterance(chunk)
        if (voice) {
          // Some engines reject voice objects they did not mint; never let
          // that silence speech entirely — fall back to the engine default
          // voice at the same calm rate/pitch.
          try {
            utter.voice = voice
            if (voice.lang) utter.lang = voice.lang
          } catch { /* keep default voice */ }
        }
        utter.rate = clean.tts_rate
        utter.pitch = clean.tts_pitch
        utter.volume = clean.tts_volume
        if (index === 0) utter.onstart = () => { if (this.session === session) this._setKey(key || '__speech__') }
        utter.onend = done
        utter.onerror = done
        window.speechSynthesis.speak(utter)
      })
    } catch {
      this._setKey(null)
      return 'unavailable'
    }
    // Optimistic indicator: some engines never fire onstart for queued text.
    if (this.session === session) this._setKey(key || '__speech__')
    return 'started'
  }
}

export const voiceEngine = new VoiceEngine()
