import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { callOptional, waitForNative } from './bridge.js'
import { Icon } from './icons.jsx'
import { extractThink } from './markdown.jsx'
import { PushToTalkRecorder, micSupported, normalizeLiveCapabilities, trimCaption } from './live.js'
import { plainSpeechText, voiceEngine } from './voice.js'

const PHASE_LABELS = {
  idle: 'Ready',
  listening: 'Listening',
  transcribing: 'Transcribing locally',
  thinking: 'Thinking',
  speaking: 'Speaking',
  capturing: 'Capturing one frame',
  preview: 'Check the frame',
  error: 'Needs attention',
  setup: 'Setup needed',
}

export function LiveOrb({ phase = 'idle', level = 0, small = false }) {
  const bars = useMemo(() => Array.from({ length: 28 }, (_, index) => {
    const wave = Math.abs(Math.sin(index * 1.73)) * 0.65 + Math.abs(Math.cos(index * 0.61)) * 0.35
    return { angle: index * (360 / 28), wave, delay: `${(index % 7) * 70}ms` }
  }), [])
  const activeLevel = Math.max(0, Math.min(1, Number(level) || 0))
  return (
    <div className={`live-orb phase-${phase}${small ? ' small' : ''}`} aria-hidden="true">
      <div className="live-orb-ring" />
      <div className="live-orb-bars">
        {bars.map((bar, index) => (
          <span
            key={index}
            style={{ transform: `rotate(${bar.angle}deg) translateY(calc(var(--orb-radius) * -1))` }}
          >
            <i style={{ height: `${7 + activeLevel * 18 * bar.wave}px`, animationDelay: bar.delay }} />
          </span>
        ))}
      </div>
      <div className="live-orb-core"><Icon name={phase === 'listening' ? 'mic' : 'live'} size={small ? 22 : 28} /></div>
    </div>
  )
}

function Caption({ label, text, tone = '' }) {
  if (!text) return null
  return (
    <div className={`live-caption ${tone}`}>
      <span>{label}</span>
      <p>{trimCaption(text)}</p>
    </div>
  )
}

export function LivePanel({
  open,
  onClose,
  onOpenRequest,
  native,
  capabilities: rawCapabilities,
  onRefreshCapabilities,
  callApi,
  pushToast,
  messages,
  streaming,
  speakingKey,
  ttsPrefs,
  onSendLive,
  onLiveTurnStarted,
  onStopGeneration,
  onOpenVoiceSettings,
  task,
}) {
  const capabilities = useMemo(
    () => normalizeLiveCapabilities(rawCapabilities, native),
    [rawCapabilities, native],
  )
  const stt = capabilities.speech_to_text
  const vision = capabilities.screen_vision
  const [phase, setPhase] = useState('idle')
  const [caption, setCaption] = useState('Hold push-to-talk, speak, then release to send.')
  const [level, setLevel] = useState(0)
  const [lastUser, setLastUser] = useState('')
  const [assistantCaption, setAssistantCaption] = useState('')
  const [pendingTranscript, setPendingTranscript] = useState('')
  const [frame, setFrame] = useState(null)
  const [frameApproved, setFrameApproved] = useState(false)
  const [speechOutput, setSpeechOutput] = useState(true)
  const [miniOpen, setMiniOpen] = useState(false)

  const recorderRef = useRef(null)
  const phaseRef = useRef(phase)
  const speechOutputRef = useRef(speechOutput)
  const pendingKeyRef = useRef('')
  const spokenKeyRef = useRef('')
  const commandSeqRef = useRef(0)
  const previousTaskRef = useRef('')
  const stopTalkRef = useRef(null)
  const commandHandlerRef = useRef(null)

  useEffect(() => { phaseRef.current = phase }, [phase])
  useEffect(() => { speechOutputRef.current = speechOutput }, [speechOutput])
  useEffect(() => { if (capabilities.mini_open) setMiniOpen(true) }, [capabilities.mini_open])

  const setPhaseAndCaption = useCallback((nextPhase, nextCaption) => {
    setPhase(nextPhase)
    if (typeof nextCaption === 'string') setCaption(nextCaption)
  }, [])

  // Keep the floating mini in sync with captions/status only. Audio and
  // screenshot bytes deliberately never enter this state object.
  useEffect(() => {
    if (!native || (!open && !miniOpen)) return undefined
    const timer = window.setTimeout(() => {
      callOptional('set_live_state', null, {
        active: open || miniOpen,
        status: speakingKey && speakingKey === pendingKeyRef.current ? 'speaking' : phase,
        caption,
        user_text: lastUser,
        assistant_text: assistantCaption,
        level,
        screen_mode: Boolean(frame),
        frame_approved: frameApproved,
        speech_output: speechOutput,
      })
    }, 40)
    return () => window.clearTimeout(timer)
  }, [native, open, miniOpen, phase, caption, lastUser, assistantCaption, level, frame, frameApproved, speechOutput, speakingKey])

  const sendTranscript = useCallback(async (text, imageDataUrl = '') => {
    const clean = String(text || '').trim()
    if (!clean) return false
    setPhaseAndCaption('thinking', imageDataUrl ? 'Sending your question with the approved frame…' : 'Thinking…')
    const key = await onSendLive(clean, { imageDataUrl })
    if (!key) {
      setPhaseAndCaption('error', 'That message could not be started. Your words are still here so you can try again.')
      return false
    }
    pendingKeyRef.current = String(key)
    onLiveTurnStarted?.(String(key), !speechOutputRef.current)
    spokenKeyRef.current = ''
    setPendingTranscript('')
    setFrame(null)
    setFrameApproved(false)
    setAssistantCaption('')
    return true
  }, [onSendLive, setPhaseAndCaption])

  const getRecorder = useCallback(() => {
    if (!recorderRef.current) {
      recorderRef.current = new PushToTalkRecorder({
        onLevel: setLevel,
        onAutoStop: () => stopTalkRef.current?.(),
      })
    }
    return recorderRef.current
  }, [])

  const startTalk = useCallback(async () => {
    if (phaseRef.current === 'listening' || streaming) return
    if (!native) {
      setPhaseAndCaption('setup', 'Vision Live voice runs in the Windows desktop app with local speech-to-text.')
      return
    }
    if (!stt.available) {
      setPhaseAndCaption('setup', stt.reason)
      return
    }
    if (!micSupported()) {
      setPhaseAndCaption('error', 'Microphone recording is not available in this window.')
      return
    }
    voiceEngine.stop()
    try {
      await getRecorder().start()
      setPhaseAndCaption('listening', 'Listening… release to send. Nothing is heard before you press or after you release.')
    } catch (error) {
      const denied = error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError'
      setPhaseAndCaption('error', denied
        ? 'Microphone access was blocked. Allow the microphone for VISION in Windows Settings, then try again.'
        : (error?.message || 'The microphone could not be started.'))
    }
  }, [getRecorder, native, setPhaseAndCaption, streaming, stt.available, stt.reason])

  const stopTalk = useCallback(async () => {
    if (phaseRef.current !== 'listening') return
    setPhaseAndCaption('transcribing', 'Transcribing on this PC…')
    let wavBase64 = null
    try {
      wavBase64 = await getRecorder().stop()
    } catch (error) {
      setPhaseAndCaption('error', error?.message || 'That voice clip could not be prepared.')
      return
    }
    setLevel(0)
    if (!wavBase64) {
      setPhaseAndCaption('error', 'I did not receive a voice clip. Hold push-to-talk while you speak.')
      return
    }
    const result = await callApi('transcribe_live_audio', { audio_base64: wavBase64, mime: 'audio/wav' })
    if (!result?.ok || !String(result.text || '').trim()) {
      setPhaseAndCaption('error', result?.message || 'I could not make out those words. Please try again.')
      if (result?.live) onRefreshCapabilities?.()
      return
    }
    const text = String(result.text).trim()
    setLastUser(text)
    setPendingTranscript(text)
    if (frame && !frameApproved) {
      setPhaseAndCaption('preview', 'Check the screen frame, then approve it or remove it before this question is sent.')
      return
    }
    if (frame && frameApproved && !vision.available) {
      setPhaseAndCaption('error', `${vision.reason} Your words were not sent; remove the frame to send them voice-only.`)
      return
    }
    await sendTranscript(text, frame && frameApproved ? frame.data_url : '')
  }, [callApi, frame, frameApproved, getRecorder, onRefreshCapabilities, sendTranscript, setPhaseAndCaption, vision.available, vision.reason])

  useEffect(() => { stopTalkRef.current = stopTalk }, [stopTalk])

  const captureFrame = useCallback(async () => {
    if (phaseRef.current === 'listening' || streaming) return
    if (!native) {
      setPhaseAndCaption('setup', 'Screen sharing is available in the Windows desktop app.')
      return
    }
    if (!capabilities.screen_capture_available) {
      setPhaseAndCaption('setup', 'Screen capture is not available on this system.')
      return
    }
    setPhaseAndCaption('capturing', 'Capturing one still frame from the primary display…')
    const result = await callApi('capture_screen_frame')
    if (!result?.ok || !result.data_url) {
      setPhaseAndCaption('error', result?.message || 'VISION could not capture the screen.')
      return
    }
    setFrame(result)
    setFrameApproved(false)
    setPhaseAndCaption('preview', vision.available
      ? 'Check this frame. It is not shared until you approve it, and it is used for one question only.'
      : 'This is only a preview. The installed VISION model cannot understand screenshots yet, so nothing will be sent as an image.')
  }, [callApi, capabilities.screen_capture_available, native, setPhaseAndCaption, streaming, vision.available])

  const stopAll = useCallback(async () => {
    recorderRef.current?.cancel()
    setLevel(0)
    voiceEngine.stop()
    if (streaming) await onStopGeneration?.()
    setPhaseAndCaption('idle', 'Stopped. Hold push-to-talk when you are ready.')
  }, [onStopGeneration, setPhaseAndCaption, streaming])

  const closeLive = useCallback(async () => {
    recorderRef.current?.cancel()
    setLevel(0)
    voiceEngine.stop()
    setFrame(null)
    setFrameApproved(false)
    setPendingTranscript('')
    if (native) {
      await callOptional('set_live_state', null, {
        active: miniOpen,
        status: 'idle',
        caption: miniOpen ? caption : '',
        level: 0,
        screen_mode: false,
        frame_approved: false,
      })
    }
    setPhaseAndCaption('idle', 'Hold push-to-talk, speak, then release to send.')
    onClose?.()
  }, [caption, miniOpen, native, onClose, setPhaseAndCaption])

  useEffect(() => () => { recorderRef.current?.cancel() }, [])

  useEffect(() => {
    if (open) onRefreshCapabilities?.()
  }, [open, onRefreshCapabilities])

  useEffect(() => {
    const previous = previousTaskRef.current
    previousTaskRef.current = String(task?.name || '')
    if (previous === 'download_stt' && !task) onRefreshCapabilities?.()
  }, [task, onRefreshCapabilities])

  // The mini window sends commands through the bridge. The main window
  // remains the only place that owns the microphone and chat submission.
  useEffect(() => {
    commandHandlerRef.current = async (action) => {
      if (action === 'open_live') { onOpenRequest?.(); return }
      if (action === 'ptt_start') { onOpenRequest?.(); await startTalk(); return }
      if (action === 'ptt_stop') { await stopTalk(); return }
      if (action === 'share_frame') { onOpenRequest?.(); await captureFrame(); return }
      if (action === 'stop') { await stopAll() }
    }
  })
  useEffect(() => {
    if (!native || (!open && !miniOpen)) return undefined
    let alive = true
    const poll = async () => {
      const result = await callOptional('get_live_state', null)
      if (!alive || !result?.command) return
      const seq = Number(result.command.seq || 0)
      if (seq > commandSeqRef.current) {
        commandSeqRef.current = seq
        await commandHandlerRef.current?.(String(result.command.action || ''))
      }
    }
    const timer = window.setInterval(poll, 250)
    poll()
    return () => { alive = false; window.clearInterval(timer) }
  }, [native, open, miniOpen])

  // Follow the chat turn this panel started: stream captions, then read
  // the final answer once with the user's saved soothing voice settings.
  useEffect(() => {
    const key = pendingKeyRef.current
    if (!key) return
    const message = messages.find((item) => item.key === key)
    if (!message) return
    const visible = plainSpeechText(extractThink(message.content || '').content || message.content || '')
    if (visible) setAssistantCaption(visible)
    if (streaming || message.streaming) {
      if (phaseRef.current !== 'listening') setPhaseAndCaption('thinking', visible ? 'VISION is answering…' : 'Thinking…')
      return
    }
    if (message.incomplete) {
      setPhaseAndCaption('error', visible || 'That answer did not finish.')
      pendingKeyRef.current = ''
      return
    }
    if (visible && speechOutputRef.current && !ttsPrefs?.tts_auto_read && spokenKeyRef.current !== key) {
      spokenKeyRef.current = key
      voiceEngine.speak(message.content, ttsPrefs, key).then((status) => {
        if (status === 'started') setPhaseAndCaption('speaking', 'Speaking… press push-to-talk to interrupt.')
        else if (status === 'no-voices') setPhaseAndCaption('error', 'No speech voices are installed on this PC yet. Captions are still shown above.')
        else if (status === 'unavailable') setPhaseAndCaption('idle', 'Answer ready. Read-aloud is not available in this window.')
        else setPhaseAndCaption('idle', 'Answer ready.')
      }).catch(() => setPhaseAndCaption('idle', 'Answer ready.'))
    } else if (phaseRef.current !== 'listening') {
      setPhaseAndCaption('idle', 'Answer ready. Hold push-to-talk to continue.')
    }
    pendingKeyRef.current = ''
  }, [messages, streaming, ttsPrefs, setPhaseAndCaption])

  useEffect(() => {
    if (speakingKey && phaseRef.current !== 'listening') setPhase('speaking')
    else if (!speakingKey && phaseRef.current === 'speaking') setPhaseAndCaption('idle', 'Hold push-to-talk to continue.')
  }, [speakingKey, setPhaseAndCaption])

  if (!open) return null

  const displayedPhase = speakingKey ? 'speaking' : phase
  const downloadBusy = task?.name === 'download_stt'
  const canSendPending = Boolean(pendingTranscript && !streaming && (!frame || frameApproved))
  const pendingNeedsVision = Boolean(frame && frameApproved && !vision.available)

  const sendPending = async () => {
    if (!pendingTranscript) return
    if (frame && !frameApproved) {
      setPhaseAndCaption('preview', 'Approve the frame or remove it before sending.')
      return
    }
    await sendTranscript(pendingTranscript, frame && frameApproved && vision.available ? frame.data_url : '')
  }

  const openMini = async () => {
    if (!native) { pushToast?.('The floating Live window runs in the desktop app.', 'error'); return }
    const result = await callApi('open_live_mini')
    if (result?.ok) { setMiniOpen(true); pushToast?.('Vision Live mini opened. It stays on top while you work.', 'success') }
  }

  const downloadStt = async () => {
    const result = await callApi('download_speech_to_text', { model_size: stt.model_size })
    if (result?.accepted) setPhaseAndCaption('setup', 'Downloading the local speech-to-text engine and voice model…')
  }

  return (
    <div className="overlay live-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) closeLive() }}>
      <section className="live-panel" role="dialog" aria-modal="true" aria-label="Vision Live">
        <header className="live-head">
          <div>
            <span className="live-eyebrow"><Icon name="live" size={15} /> Vision Live</span>
            <h2>Talk with VISION</h2>
            <p>Push-to-talk voice on this PC. Screen sharing is one approved still frame at a time.</p>
          </div>
          <button type="button" className="icon-btn" aria-label="Close Vision Live" onClick={closeLive}><Icon name="x" size={17} /></button>
        </header>

        <div className="live-mode-row" aria-label="Vision Live capabilities">
          <span className={`live-mode ${stt.available ? 'ready' : ''}`}><Icon name="mic" size={14} /> Voice chat {stt.available ? 'ready' : 'setup needed'}</span>
          <span className={`live-mode ${vision.available ? 'ready' : ''}`}><Icon name="screen" size={14} /> Voice + screen {vision.available ? 'ready' : 'model upgrade needed'}</span>
          <span className="live-status">{PHASE_LABELS[displayedPhase] || 'Ready'}</span>
        </div>

        <LiveOrb phase={displayedPhase} level={displayedPhase === 'listening' ? level : displayedPhase === 'speaking' ? 0.55 : 0.08} />

        <div className="live-captions" aria-live="polite">
          <Caption label="You" text={lastUser} tone="user" />
          <Caption label="VISION" text={assistantCaption} tone="assistant" />
          <div className={`live-caption status ${displayedPhase === 'error' ? 'error' : ''}`}><span>Live</span><p>{caption}</p></div>
        </div>

        {!stt.available ? (
          <div className="live-setup">
            <strong>Local voice setup</strong>
            <p>{native ? stt.reason : 'Open VISION Desktop on Windows to install and use local speech-to-text.'} The one-time download uses the internet; after that, listening works offline and audio stays on this PC.</p>
            {downloadBusy ? <><p className="empty-note">{task.message || 'Downloading…'}{task.percent != null ? ` ${task.percent}%` : ''}</p><div className="progress-line"><i style={{ transform: `scaleX(${Math.max(0, Math.min(100, Number(task.percent) || 0)) / 100})` }} /></div></> : null}
            <div className="action-row">
              <button type="button" className="btn primary" disabled={!native || downloadBusy} onClick={downloadStt}><Icon name="download" size={15} /> Download local voice model</button>
              <button type="button" className="btn ghost" onClick={() => { closeLive(); onOpenVoiceSettings?.() }}>Voice settings</button>
            </div>
          </div>
        ) : null}

        {frame ? (
          <div className="live-frame-preview">
            <img src={frame.data_url} alt="Preview of the one screen frame you captured" decoding="async" />
            <div>
              <strong>One screen frame · {frame.width}×{frame.height}</strong>
              <p>{vision.available
                ? 'Approve it only if everything visible is safe to share with the local model. It will be used once and is not saved.'
                : `${vision.reason} This preview is not sent anywhere.`}</p>
              <div className="action-row">
                <button type="button" className="btn primary" disabled={!vision.available || frameApproved} onClick={() => { setFrameApproved(true); setPhaseAndCaption('idle', 'Frame approved for your next question only.') }}>{frameApproved ? 'Approved for next question' : 'Approve frame'}</button>
                <button type="button" className="btn" onClick={captureFrame}>Retake</button>
                <button type="button" className="btn ghost" onClick={() => { setFrame(null); setFrameApproved(false); setPhaseAndCaption(pendingTranscript ? 'idle' : phase, pendingTranscript ? 'Frame removed. You can send your words voice-only.' : caption) }}>Remove</button>
              </div>
            </div>
          </div>
        ) : null}

        <div className="live-controls">
          <button
            type="button"
            className={`live-talk ${displayedPhase === 'listening' ? 'listening' : ''}`}
            disabled={streaming || displayedPhase === 'transcribing'}
            onPointerDown={(event) => { event.preventDefault(); try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* pointer capture is optional */ } startTalk() }}
            onPointerUp={(event) => { event.preventDefault(); stopTalk() }}
            onPointerCancel={stopTalk}
            onKeyDown={(event) => { if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) { event.preventDefault(); startTalk() } }}
            onKeyUp={(event) => { if (event.key === ' ' || event.key === 'Enter') { event.preventDefault(); stopTalk() } }}
            aria-label="Hold to talk"
          >
            <Icon name="mic" size={21} />
            <span>{displayedPhase === 'listening' ? 'Release to send' : 'Hold to talk'}</span>
          </button>
          <button type="button" className="btn" disabled={displayedPhase === 'listening' || streaming} onClick={captureFrame}><Icon name="screen" size={15} /> Share screen frame</button>
          <button type="button" className="btn" onClick={openMini}><Icon name="live" size={15} /> Floating mini</button>
          <button type="button" className="btn danger" onClick={stopAll}><Icon name="stop" size={14} /> Stop</button>
        </div>

        {pendingTranscript ? (
          <div className="live-pending">
            <span>Heard: “{trimCaption(pendingTranscript, 220)}”</span>
            <button type="button" className="btn primary" disabled={!canSendPending} onClick={sendPending}>
              {frame && frameApproved && vision.available ? 'Send with approved frame' : pendingNeedsVision ? 'Send voice only' : 'Send question'}
            </button>
          </div>
        ) : null}

        <div className="live-footer">
          <label className="check-row"><input type="checkbox" checked={speechOutput} onChange={(event) => { setSpeechOutput(event.target.checked); if (!event.target.checked) voiceEngine.stop() }} /> Speak answers</label>
          <span><Icon name="shield" size={13} /> Mic off until held · audio/frames not saved · screen frames stay local · no clicks or typing</span>
        </div>
      </section>
    </div>
  )
}

export function LiveMiniApp() {
  const [native, setNative] = useState(false)
  const [state, setState] = useState({ active: false, status: 'idle', caption: '', user_text: '', assistant_text: '', level: 0 })
  const [pressed, setPressed] = useState(false)

  useEffect(() => {
    try { document.documentElement.dataset.theme = window.localStorage.getItem('vision.theme') || 'dark' } catch { document.documentElement.dataset.theme = 'dark' }
    let alive = true
    waitForNative(3000).then((available) => { if (alive) setNative(available) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    if (!native) return undefined
    let alive = true
    const poll = async () => {
      const result = await callOptional('get_live_state', null)
      if (alive && result?.live_state) setState(result.live_state)
    }
    const timer = window.setInterval(poll, 250)
    poll()
    return () => { alive = false; window.clearInterval(timer) }
  }, [native])

  const action = async (name) => { await callOptional('live_mini_action', null, name) }
  const phase = state.status || 'idle'
  const caption = state.assistant_text || state.caption || (state.active ? 'Hold Talk and speak.' : 'Open Vision Live in the main window to begin.')

  return (
    <main className="live-mini-app">
      <header className="live-mini-head pywebview-drag-region">
        <span><Icon name="live" size={15} /> VISION Live</span>
        <button type="button" className="icon-btn small" aria-label="Close mini" onClick={() => callOptional('close_live_mini', null)}><Icon name="x" size={14} /></button>
      </header>
      <LiveOrb phase={pressed ? 'listening' : phase} level={state.level || (phase === 'speaking' ? 0.55 : 0.08)} small />
      <div className="live-caption mini-caption"><span>{PHASE_LABELS[phase] || 'Ready'}</span><p>{trimCaption(caption, 320)}</p></div>
      {state.user_text ? <div className="live-caption mini-caption user"><span>You</span><p>{trimCaption(state.user_text, 220)}</p></div> : null}
      <div className="live-mini-actions">
        <button
          type="button"
          className={`live-talk mini ${pressed ? 'listening' : ''}`}
          disabled={!native}
          onPointerDown={(event) => { event.preventDefault(); setPressed(true); action('ptt_start') }}
          onPointerUp={(event) => { event.preventDefault(); setPressed(false); action('ptt_stop') }}
          onPointerCancel={() => { setPressed(false); action('ptt_stop') }}
          onKeyDown={(event) => { if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) { setPressed(true); action('ptt_start') } }}
          onKeyUp={(event) => { if (event.key === ' ' || event.key === 'Enter') { setPressed(false); action('ptt_stop') } }}
        ><Icon name="mic" size={17} /> Hold to talk</button>
        <button type="button" className="btn small" disabled={!native} onClick={() => action('share_frame')}><Icon name="screen" size={13} /> Frame</button>
        <button type="button" className="btn small danger" disabled={!native} onClick={() => action('stop')}><Icon name="stop" size={12} /> Stop</button>
      </div>
      {!native ? <p className="live-mini-note">Desktop bridge unavailable.</p> : <p className="live-mini-note">Local voice · no audio saved · read-only frames</p>}
    </main>
  )
}
