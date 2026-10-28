import React, { Suspense, lazy, memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api as bridgeApi, callOptional, on, waitForNative } from './bridge.js'
import { Icon, LogoMark, toolCategory, TOOL_ICONS } from './icons.jsx'
import { Markdown, extractThink } from './markdown.jsx'
import { Sidebar } from './Sidebar.jsx'
import { systemStatusPayload } from './widgetData.js'
import { demoConversations, demoLibrary, demoMessages, demoProjects, demoServer, demoUpdates } from './demo.js'
import { DEFAULT_TTS, SAMPLE_TEXT, VOICE_STYLES, listVoices, loadVoices, normalizeTts, pickVoice, rankVoices, speechAvailable, styleGender, voiceEngine, voiceOptionLabel } from './voice.js'
import { LivePanel } from './LivePanel.jsx'
import { STT_LANGUAGE_OPTIONS, normalizeLiveCapabilities } from './live.js'

const GenWidget = lazy(() => import('./widgets.jsx').then((module) => ({ default: module.GenWidget })))
const LibraryPanel = lazy(() => import('./LibraryPanel.jsx'))

const uid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`

const PERF_PROFILE_COPY = {
  auto: 'VISION checks this PC and uses Low-end CPU mode on smaller machines, Balanced otherwise.',
  balanced: 'Standard engine settings for typical PCs. Uses the graphics and context settings below exactly as set.',
  performance: 'Extra tuning for powerful PCs: engine threads matched to your CPU cores and long prompts processed in bigger batches.',
  low_end: 'Runs the model on the CPU only (the graphics slider is ignored) with a smaller context and compressed memory, so it fits PCs like an Intel i3 with 8 GB memory. Answers are slower, but the app stays responsive.',
}

export function parseArgs(raw) {
  if (raw && typeof raw === 'object') return raw
  if (typeof raw !== 'string' || !raw.trim()) return {}
  try { return JSON.parse(raw) } catch { return raw }
}
const pretty = (value) => {
  const parsed = typeof value === 'string' ? parseArgs(value) : value
  return typeof parsed === 'string' ? parsed : JSON.stringify(parsed ?? {}, null, 2)
}
const oneLine = (value, limit = 150) => {
  const parsed = parseArgs(value)
  const text = String(typeof parsed === 'string' ? parsed : JSON.stringify(parsed) || '{}').replace(/\s+/g, ' ')
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}
function formatBytes(value) {
  const n = Number(value) || 0
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / 1024 ** i).toFixed(i ? 2 : 0)} ${units[i]}`
}
function storageGet(key, fallback = '') {
  try { return window.localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function storageSet(key, value) {
  try { window.localStorage.setItem(key, value) } catch { /* WebView storage can be unavailable in restricted profiles. */ }
}
function storageJson(key, fallback) {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) || 'null')
    return parsed && typeof parsed === 'object' ? parsed : fallback
  } catch { return fallback }
}
function storageSetJson(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)) } catch { /* Keep the in-memory UI state. */ }
}
const TITLE_STOPWORDS = new Set('a about after all also an and any are as at be because been before being but by can could did do does for from had has have how i if in into is it its just me my of on or our please should so than that the their them then there these this to too under up us was we were what when where which who why will with would you your'.split(' '))
const TITLE_COMMAND_WORDS = new Set('analyze analyse answer build calculate check compare create describe design draw explain find fix generate give help improve list make open plan prepare review show solve summarize summarise tell write'.split(' '))
const TITLE_GENERIC_WORDS = new Set('answer below current details example following here information result results summary thing things value values'.split(' '))
function titleCaseWord(word) {
  if (word.length <= 5 && word === word.toUpperCase() && /[A-Z]/.test(word)) return word
  if (/[A-Z]/.test(word.slice(1))) return word
  return word.slice(0, 1).toUpperCase() + word.slice(1)
}
function titleWords(text, dropCommands = false) {
  const cleaned = String(text || '')
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/[#>*_~|[\](){}]/g, ' ')
  const out = []
  const seen = new Set()
  for (const raw of cleaned.match(/[A-Za-z0-9][A-Za-z0-9'’.-]*/g) || []) {
    const word = raw.replace(/^['’. -]+|['’. -]+$/g, '')
    const lower = word.toLowerCase()
    if (!word || seen.has(lower) || TITLE_STOPWORDS.has(lower) || TITLE_GENERIC_WORDS.has(lower)) continue
    if (dropCommands && TITLE_COMMAND_WORDS.has(lower)) continue
    seen.add(lower)
    out.push(word)
  }
  return out
}
export function autoConversationTitle(prompt, answer = '') {
  const words = titleWords(prompt, true)
  if (words.length < 3) {
    const seen = new Set(words.map((word) => word.toLowerCase()))
    for (const word of titleWords(answer)) {
      if (!seen.has(word.toLowerCase())) { words.push(word); seen.add(word.toLowerCase()) }
      if (words.length >= 4) break
    }
  }
  return words.length ? words.slice(0, 5).map(titleCaseWord).join(' ') : String(prompt || '').trim().slice(0, 48) || 'New chat'
}
function timestampMs(value) {
  if (value == null || value === '') return 0
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return 0
    return value < 1e10 ? value * 1000 : value
  }
  const text = String(value).trim()
  if (!text) return 0
  if (/^-?\d+(?:\.\d+)?$/.test(text)) return timestampMs(Number(text))
  const parsed = Date.parse(text)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}
function formatDate(value) {
  const ms = timestampMs(value)
  if (!ms) return ''
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
function formatDateFull(value) {
  const ms = timestampMs(value)
  if (!ms) return ''
  return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
function dateGroup(value, pinned) {
  if (pinned) return 'Pinned'
  const ms = timestampMs(value)
  if (!ms) return 'Earlier'
  const d = new Date(ms)
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  if (day >= start) return 'Today'
  if (day >= start - 86400000) return 'Yesterday'
  return 'Earlier'
}
function displayFileName(path) {
  const parts = String(path || '').split(/[\\/]/).filter(Boolean)
  return parts.length ? parts[parts.length - 1] : 'Local document'
}
function displayHost(url) {
  try { return new URL(String(url || '')).hostname || 'Web source' } catch { return 'Web source' }
}
function sourceDisplayTitle(source = {}) {
  const title = String(source.title || '').trim()
  if (title && !/^https?:\/\//i.test(title) && !/^[a-z]:\\/i.test(title) && !title.includes('\\')) return title
  if (source.url) return displayHost(source.url)
  if (source.path) return displayFileName(source.path)
  return title || 'Source'
}
function sourceDisplayMeta(source = {}) {
  if (source.url) return displayHost(source.url)
  if (source.path) return source.locator ? `Local document · ${source.locator}` : 'Local document'
  return source.locator || source.fetched_at || ''
}
function formatDuration(ms, fallback) {
  const n = Number(ms)
  if (!Number.isFinite(n) || n < 0) return fallback || ''
  return n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(n < 10000 ? 1 : 0)} s`
}
function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'
}

function normalizeMessages(history = []) {
  const out = []
  for (const message of history) {
    if (message.role === 'user') {
      out.push({ key: `m${message.id || uid()}`, role: 'user', content: message.content || '', tools: [] })
    } else if (message.role === 'assistant') {
      const calls = Array.isArray(message.tool_calls) ? message.tool_calls : []
      const tools = calls.map((call) => {
        const fn = call.function || {}
        return {
          id: String(call.id || call.tool_call_id || uid()), name: fn.name || call.name || 'tool',
          args: parseArgs(fn.arguments ?? call.arguments ?? {}), status: 'running', startedAt: Date.now(),
        }
      })
      const last = out[out.length - 1]
      if (!tools.length && last?.role === 'assistant' && last.tools?.length) {
        last.content = [last.content, message.content || ''].filter(Boolean).join('\n\n')
        last.reasoning = [last.reasoning, message.reasoning || ''].filter(Boolean).join('\n\n')
        last.incomplete = Boolean(message.incomplete || last.incomplete)
      } else {
        out.push({ key: `m${message.id || uid()}`, role: 'assistant', content: message.content || '', reasoning: message.reasoning || '', incomplete: Boolean(message.incomplete), tools, sources: [] })
      }
    } else if (message.role === 'tool') {
      const id = String(message.tool_call_id || '')
      let host = [...out].reverse().find((item) => item.role === 'assistant' && item.tools?.some((tool) => String(tool.id) === id))
      if (!host) {
        host = { key: `m${message.id || uid()}`, role: 'assistant', content: '', tools: [] }
        out.push(host)
      }
      let tool = host.tools.find((item) => String(item.id) === id)
      if (!tool) { tool = { id, name: message.name || 'tool', args: {}, status: 'running' }; host.tools.push(tool) }
      const parsed = parseArgs(message.content || '')
      Object.assign(tool, {
        status: parsed && typeof parsed === 'object' && parsed.error ? 'error' : 'success',
        content: message.content || '', raw: parsed,
        summary: parsed && typeof parsed === 'object' && parsed.summary ? parsed.summary : 'Saved tool result',
      })
    }
  }
  return out
}

function Reasoning({ text, streaming }) {
  const [open, setOpen] = useState(false)
  if (!text) return null
  return (
    <div className={`reasoning ${open ? 'open' : ''}`}>
      <button type="button" className="reasoning-btn" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <Icon name="spark" size={13} />
        <span>{streaming ? 'Reasoning…' : 'Reasoning'}</span>
        {streaming ? <span className="dots"><i /><i /><i /></span> : null}
        <span className="chev"><Icon name="chevR" size={13} /></span>
      </button>
      {open ? <div className="reasoning-body">{text}</div> : null}
    </div>
  )
}

const ReasoningMemo = memo(Reasoning)

const ToolCard = memo(function ToolCard({ tool }) {
  const [open, setOpen] = useState(false)
  const [, force] = useState(0)
  useEffect(() => {
    if (tool.status !== 'running') return undefined
    const timer = window.setInterval(() => force((v) => v + 1), 300)
    return () => window.clearInterval(timer)
  }, [tool.status])
  const widget = useMemo(() => tool.genui || systemStatusPayload(tool), [tool])
  const mcpMatch = /^mcp__(.+?)__(.+)$/.exec(tool.name || '')
  const duration = tool.status === 'running' ? formatDuration(Date.now() - (tool.startedAt || Date.now()), 'running') : formatDuration(tool.durationMs, '')
  return (
    <article className={`tool-card ${tool.status}`}>
      <button type="button" className="tool-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="tool-icon"><Icon name={TOOL_ICONS[toolCategory(tool.name)] || 'puzzle'} size={16} /></span>
        <span className="tool-main">
          <span className="tool-name-row"><span className="tool-name" title={tool.name}>{mcpMatch ? mcpMatch[2] : tool.name}</span>{mcpMatch ? <span className="tool-badge" title={`MCP server: ${mcpMatch[1]}`}>{mcpMatch[1]}</span> : null}<span className="tool-dur">{duration}</span></span>
          <span className="tool-args">{tool.status === 'running' ? 'Working…' : tool.summary || 'Open for details'}</span>
        </span>
        <span className="tool-status">{tool.status === 'running' ? <span className="spinner" /> : <Icon name={tool.status === 'error' ? 'x' : 'check'} size={16} />}</span>
      </button>
      {widget ? <div className="widget-slot"><Suspense fallback={<div className="widget-loading"><span className="spinner" />Loading result…</div>}><GenWidget payload={widget} /></Suspense></div> : null}
      {open ? (
        <div className="tool-details">
          <p className="tool-summary">{tool.summary || (tool.status === 'running' ? 'Running…' : 'Completed')}</p>
          <div className="detail-label">Arguments</div><pre className="json-block">{pretty(tool.args || {})}</pre>
          {tool.status !== 'running' ? <><div className="detail-label">{tool.status === 'error' ? 'Error / result' : 'Result'}</div><pre className="json-block">{pretty(tool.raw ?? tool.content ?? '')}</pre></> : null}
        </div>
      ) : null}
    </article>
  )
})

function MessageView({ message, handlers, latest = false, speaking = false }) {
  if (message.role === 'user') {
    const userHandlers = { ...handlers, onSource: undefined }
    return <div className="msg-row user"><div className="user-bubble"><Markdown text={message.content} {...userHandlers} /></div></div>
  }
  const extracted = extractThink(message.content || '')
  const reasoning = [message.reasoning, extracted.reasoning].filter(Boolean).join('\n\n')
  const content = extracted.content
  return (
    <div className="msg-row assistant"><article className="msg">
      <div className="avatar"><Icon name="logo" size={16} strokeWidth={2} /></div>
      <div className="msg-body">
        <div className="msg-meta"><span>Vision</span>{message.incomplete ? <span className="incomplete">Incomplete</span> : null}</div>
        {message.skills?.length ? <div className="skill-chips">{message.skills.map((name) => <span className="skill-chip" key={name}>skill: {name} used</span>)}</div> : null}
        <ReasoningMemo text={reasoning} streaming={message.streaming} />
        {message.tools?.length ? <div className="tool-stack">{message.tools.map((tool) => <ToolCard key={tool.id || tool.name} tool={tool} />)}</div> : null}
        {content || message.streaming ? <><Markdown text={content} {...handlers} />{message.streaming ? <span className="stream-cursor" /> : null}</> : null}
        {!message.streaming ? (
          <div className="msg-actions">
            <button type="button" className="icon-btn small" title="Copy" onClick={() => handlers.onCopy?.(content)}><Icon name="copy" size={15} /></button>
            <button type="button" className={`icon-btn small${speaking ? ' speaking' : ''}`} title={speaking ? 'Stop reading' : 'Listen — read aloud'} aria-label={speaking ? 'Stop reading' : 'Listen — read aloud'} aria-pressed={speaking} onClick={() => handlers.onListen?.(content, message.key)}><Icon name={speaking ? 'stop' : 'speaker'} size={15} /></button>
            {latest ? <>
              <button type="button" className="icon-btn small" title="Regenerate" onClick={handlers.onRegenerate}><Icon name="refresh" size={15} /></button>
              <button type="button" className="icon-btn small" title="Not helpful" onClick={handlers.onFeedback}><Icon name="thumb" size={15} /></button>
              <button type="button" className="icon-btn small" title="Correct this" onClick={handlers.onCorrect}><Icon name="pencil" size={15} /></button>
            </> : null}
            {message.sources?.length ? <button type="button" className="btn small ghost sources-link" onClick={() => handlers.onSources?.(message.sources)}>Sources</button> : null}
          </div>
        ) : null}
      </div>
    </article></div>
  )
}

// Streaming replaces only the active assistant object. Memoizing completed
// rows prevents every historical Markdown/tool widget from being parsed and
// reconciled again on each 30 ms stream flush.
const Message = memo(MessageView)

const Composer = memo(function Composer({ value, setValue, onSubmit, streaming, attachments, removeAttachment, chooseAttachments, endpoint, inputRef, hero = false, placeholder = '', agentMode = false, onToggleAgent = null, onOpenLive = null }) {
  const areaRef = useRef(null)
  useEffect(() => {
    const node = areaRef.current
    if (!node) return
    node.style.height = 'auto'
    node.style.height = `${Math.min(Math.max(node.scrollHeight, 28), 184)}px`
    node.style.overflowY = node.scrollHeight > 184 ? 'auto' : 'hidden'
  }, [value])
  const tokens = Math.ceil(value.length / 4) + Math.ceil(attachments.reduce((sum, item) => sum + (Number(item.size) || 0), 0) / 4)
  return (
    <div className="composer-outer">
      {attachments.length ? (
        <div className="context-chips">{attachments.map((item) => (
          <span className="context-chip" key={item.id}><Icon name="file" size={13} /><span>{item.name || 'Attachment'} · {formatBytes(item.size)}</span>
            <button type="button" aria-label="Remove attachment" onClick={() => removeAttachment(item)}><Icon name="x" size={12} /></button></span>))}
        </div>
      ) : null}
      <form className="composer" onSubmit={(event) => { event.preventDefault(); onSubmit() }}>
        <textarea
          ref={(node) => { areaRef.current = node; if (inputRef) inputRef.current = node }}
          value={value} rows={1} placeholder={agentMode ? 'Give VISION a goal…' : (placeholder || (hero ? 'How can I help you today?' : 'Message VISION…'))} aria-label="Message VISION"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit() } }}
        />
        <div className="composer-row">
          <button type="button" className="attach-btn" title="Attach text files" aria-label="Attach text files" disabled={streaming} onClick={chooseAttachments}><Icon name="plus" size={18} /></button>
          {onOpenLive ? <button type="button" className="attach-btn live-composer-btn" title="Open Vision Live voice chat" aria-label="Open Vision Live" onClick={onOpenLive}><Icon name="live" size={17} /></button> : null}
          <span className="model-chip" title={endpoint.label || endpoint.kind}><span className={`dot ${endpoint.cloud ? 'warn' : 'healthy'}`} />{endpoint.cloud ? 'CLOUD' : 'LOCAL'}</span>
          {onToggleAgent ? (
            <span className="mode-toggle" role="group" aria-label="Response mode">
              <button type="button" className={!agentMode ? 'active' : ''} onClick={() => onToggleAgent(false)}>Chat</button>
              <button type="button" className={agentMode ? 'active agent' : ''} title="Agent mode: VISION plans and works through the goal step by step with tools. Safe actions run without asking each time; risky actions still ask. Stop anytime." onClick={() => onToggleAgent(true)}>Agent</button>
            </span>
          ) : null}
          <span className="grow" />
          <span className="token-count">~{tokens.toLocaleString()} tokens</span>
          <button type="submit" className={`send-btn ${streaming ? 'stop' : ''}`} title={streaming ? 'Stop generating' : 'Send'} aria-label={streaming ? 'Stop generating' : 'Send'} disabled={!streaming && !value.trim()}>
            <Icon name={streaming ? 'stop' : 'arrowUp'} size={18} strokeWidth={2} />
          </button>
        </div>
      </form>
      {!hero ? <div className="composer-hint"><span><span className="kbd">Enter</span> send · <span className="kbd">Shift</span>+<span className="kbd">Enter</span> new line</span><span><span className="kbd">Ctrl</span>+<span className="kbd">K</span> commands</span></div> : null}
    </div>
  )
})

function StatusRows({ rows }) {
  return <div className="status-card"><dl>{rows.map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v == null || v === '' ? '—' : String(v)}</dd></React.Fragment>)}</dl></div>
}

function TechnicalDetails({ label = 'Advanced details', rows = null, children = null }) {
  if (!rows?.length && !children) return null
  return (
    <details className="technical-details">
      <summary>{label}</summary>
      <div className="technical-body">
        {rows?.length ? <StatusRows rows={rows} /> : null}
        {children}
      </div>
    </details>
  )
}

function serverRows(server = {}) {
  return [
    ['Status', server.state || 'Not checked'],
    ['Local model', server.model_exists ? `Installed (${formatBytes(server.model_size)})` : 'Not installed'],
    ['Runtime', server.binary_checked ? (server.binary ? (server.build_supported ? 'Ready' : 'Update required') : 'Not installed') : 'Checking…'],
    ['Internet', server.internet === 'online' ? 'Connected' : server.internet === 'offline' ? 'Offline — local model only' : 'Checking…'],
  ]
}

function modelUpdateRows(updates = {}) {
  const model = updates?.channels?.model || {}
  const sha = String(updates?.installed_model_sha || '')
  const installed = model.installed_version && model.installed_version !== 'Not installed'
    ? model.installed_version
    : (sha ? sha.slice(0, 12) : 'Not installed')
  const lastChecked = updates?.last_checked || model.last_checked
  return [
    ['Model updates', 'Updated weekly'],
    ['Installed model (SHA)', installed],
    ['Last checked', lastChecked ? formatDateFull(lastChecked) : 'Not checked yet'],
    ['Model last changed', updates?.model_updated_at ? formatDateFull(updates.model_updated_at) : 'Original install'],
  ]
}

function serverTechnicalRows(server = {}) {
  const build = server.build ? `b${server.build}` : 'Unknown'
  return [
    ['Process', server.running ? 'Running' : 'Stopped'],
    ['Health failures', server.failures ?? 0],
    ['Model file', server.model_path ? displayFileName(server.model_path) : 'Not configured'],
    ['Runtime file', server.binary ? displayFileName(server.binary) : 'Not found'],
    ['Runtime build', `${build} (minimum b${server.minimum_build || 11815})`],
    ['Last exit code', server.exit_code ?? '—'],
  ]
}

function telemetryStatusLabel(status = {}) {
  const label = {
    never: 'Never uploaded',
    success: 'Uploaded successfully',
    failed: 'Upload failed — queued locally',
    queued_offline: 'Queued for a later launch',
    skipped_no_token: 'No upload key saved',
  }[status.state] || 'Not checked'
  return status.at ? `${label} · ${formatDate(status.at)}` : label
}

function updateStatusLabel(row = {}) {
  if (row.status === 'downloading') return `Downloading ${Math.max(0, Math.min(100, Math.round(Number(row.progress) || 0)))}%`
  return row.status_label || {
    up_to_date: 'Up to date',
    update_available: 'Update available',
    verifying: 'Verifying',
    restart_to_apply: 'Restart to apply',
    failed: 'Failed',
  }[row.status] || 'Up to date'
}

const UpdateChannelRow = memo(function UpdateChannelRow({ channel, label, description, icon, row = {}, busy = false, onCheck, onDownload, onRestart, onRollback }) {
  const status = row.status || 'up_to_date'
  const progress = Math.max(0, Math.min(100, Number(row.progress) || 0))
  const canRollback = channel === 'app' && Boolean(row.previous_version)
  return (
    <article className="update-row">
      <div className="update-row-icon"><Icon name={icon} size={19} /></div>
      <div className="update-row-main">
        <div className="update-row-head">
          <h4>{label}</h4>
          <span className={`update-pill ${status}`} title={status === 'failed' && row.error ? row.error : undefined}>{status === 'failed' ? 'Failed' : updateStatusLabel(row)}</span>
        </div>
        <p>{description}</p>
        <dl className="update-meta">
          <div><dt>Installed</dt><dd>{row.installed_version || '—'}</dd></div>
          <div><dt>Available</dt><dd>{row.available_version || '—'}</dd></div>
          {row.available_size ? <div><dt>Size</dt><dd>{formatBytes(row.available_size)}</dd></div> : null}
          <div><dt>Last checked</dt><dd>{row.last_checked ? formatDate(row.last_checked) : 'Not checked'}</dd></div>
        </dl>
        {status === 'downloading' || status === 'verifying' ? <div className="progress-line update-progress"><i style={{ transform: `scaleX(${Math.max(0, Math.min(100, Number(progress) || 0)) / 100})` }} /></div> : null}
        {row.error ? <p className="update-error">{row.error}</p> : null}
      </div>
      <div className="update-actions">
        <button type="button" className="btn small ghost" disabled={busy} onClick={() => onCheck(channel)}>Check now</button>
        {status === 'update_available' ? <button type="button" className="btn small primary" disabled={busy} onClick={() => onDownload(channel, row.available_version)}>{channel === 'config' ? 'Apply now' : 'Download update'}</button> : null}
        {status === 'restart_to_apply' ? <button type="button" className="btn small primary" disabled={busy} onClick={onRestart}>Restart to apply</button> : null}
        {status === 'failed' ? <button type="button" className="btn small" disabled={busy} onClick={() => onCheck(channel)}>Try again</button> : null}
        {canRollback ? <button type="button" className="btn small ghost" disabled={busy} onClick={onRollback}>Roll back</button> : null}
      </div>
    </article>
  )
})

export default function App() {
  const gallery = useMemo(() => new URLSearchParams(window.location.search).has('gallery'), [])
  const [native, setNative] = useState(false)
  const [ready, setReady] = useState(false)
  const [theme, setTheme] = useState(() => storageGet('vision.theme', 'dark') || 'dark')
  const [collapsed, setCollapsed] = useState(() => storageGet('vision.sidebarCollapsed') === '1')
  const [appInfo, setAppInfo] = useState({})
  const [config, setConfig] = useState({})
  const [credentials, setCredentials] = useState({})
  const [paths, setPaths] = useState({})
  const [projects, setProjects] = useState([])
  const [skills, setSkills] = useState([])
  const [extensions, setExtensions] = useState({ servers: [], connectors: [], dynamic_tool_count: 0, messages: [] })
  const [conversations, setConversations] = useState([])
  const [currentId, setCurrentId] = useState('')
  const [currentConversation, setCurrentConversation] = useState(null)
  const [selectedProject, setSelectedProject] = useState('')
  const [projectFilter, setProjectFilter] = useState(() => storageGet('vision.projectFilter', 'all') || 'all')
  const [collapsedProjects, setCollapsedProjects] = useState(() => storageJson('vision.collapsedProjects', {}))
  const [search, setSearch] = useState('')
  const [searchRows, setSearchRows] = useState(null)
  const [messages, setMessages] = useState([])
  const [sources, setSources] = useState([])
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [highlightSource, setHighlightSource] = useState('')
  const [attachments, setAttachments] = useState([])
  const [libraryItems, setLibraryItems] = useState([])
  const [libraryCount, setLibraryCount] = useState(0)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [libraryLoading, setLibraryLoading] = useState(false)
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [endpoint, setEndpoint] = useState({ cloud: false, kind: 'local', label: '' })
  const [server, setServer] = useState({})
  const [serverLogs, setServerLogs] = useState([])
  const [updates, setUpdates] = useState(demoUpdates)
  const [telemetry, setTelemetry] = useState({ opt_in: false, pending_count: 0, status: { state: 'never', at: null, message: 'No upload has been recorded yet.', uploaded_events: 0, pending_events: 0 }, report: null })
  const [telemetryPreview, setTelemetryPreview] = useState(null)
  const [telemetryClearArmed, setTelemetryClearArmed] = useState(false)
  const [banner, setBanner] = useState('')
  const [toasts, setToasts] = useState([])
  const [task, setTask] = useState(null)
  const [overlay, setOverlay] = useState('') // settings | server | activity | setup | palette
  const [settingsTab, setSettingsTab] = useState('general')
  const [form, setForm] = useState(null)
  const [agentMode, setAgentMode] = useState(() => { try { return localStorage.getItem('vision-agent-mode') === '1' } catch { return false } })
  const agentModeRef = useRef(agentMode)
  const toggleAgentMode = useCallback((next) => { const value = Boolean(next); agentModeRef.current = value; setAgentMode(value); try { localStorage.setItem('vision-agent-mode', value ? '1' : '0') } catch {} }, [])
  const [extEditor, setExtEditor] = useState(null)
  const [skillEditor, setSkillEditor] = useState(null)
  const [extTab, setExtTab] = useState('mcp')
  const [connectorDirectory, setConnectorDirectory] = useState([])
  const [skillPreview, setSkillPreview] = useState(null)
  const [extDeleteArmed, setExtDeleteArmed] = useState('')
  const [elicitValues, setElicitValues] = useState({})
  const [availableModels, setAvailableModels] = useState([])
  const [hardware, setHardware] = useState(null)
  const [codeCheckpoints, setCodeCheckpoints] = useState([])
  const [restoreCheckpointArmed, setRestoreCheckpointArmed] = useState('')
  const [activity, setActivity] = useState(null)
  const [clearArmed, setClearArmed] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  const [confirmQueue, setConfirmQueue] = useState([])
  const [promptState, setPromptState] = useState(null)
  const [promptValue, setPromptValue] = useState('')
  const [showJump, setShowJump] = useState(false)
  const [setupGpu, setSetupGpu] = useState(99)
  // Read-aloud: saved prefs (backend config is the source of truth once
  // loaded; localStorage seeds first paint and serves preview/gallery
  // mode), the installed-voice list, and which message is being spoken.
  const [ttsPrefs, setTtsPrefs] = useState(() => normalizeTts(storageJson('vision.tts', DEFAULT_TTS)))
  const [ttsVoices, setTtsVoices] = useState(() => listVoices())
  const [speakingKey, setSpeakingKey] = useState(null)
  const [liveOpen, setLiveOpen] = useState(false)
  const [liveCapabilities, setLiveCapabilities] = useState(() => normalizeLiveCapabilities(null, false))
  const [reduceMotion, setReduceMotion] = useState(() => storageGet('vision.reduceMotion') === '1')
  // At or below 900px the sidebar becomes the icon rail regardless of the
  // stored preference, so snapped and very small windows stay usable.
  const [narrow, setNarrow] = useState(() => { try { return window.matchMedia('(max-width: 900px)').matches } catch { return false } })

  const nativeRef = useRef(false)
  const streamingRef = useRef(false)
  const currentIdRef = useRef('')
  const assistantKeyRef = useRef('')
  const stickRef = useRef(true)
  const transcriptRef = useRef(null)
  const inputRef = useRef(null)
  const simTimersRef = useRef([])
  const messagesRef = useRef([])
  const draftRef = useRef('')
  const projectsLoadedRef = useRef(false)
  const scrollFrameRef = useRef(0)
  const ttsPrefsRef = useRef(ttsPrefs)
  const speakingKeyRef = useRef(null)
  const autoSpokenKeyRef = useRef('')
  const liveSpeechRef = useRef({ key: '', muted: false })
  const prevStreamingRef = useRef(false)

  const effectiveCollapsed = collapsed || narrow

  useEffect(() => { nativeRef.current = native }, [native])
  useEffect(() => { streamingRef.current = streaming }, [streaming])
  useEffect(() => { currentIdRef.current = currentId }, [currentId])
  useEffect(() => { document.documentElement.dataset.theme = theme; storageSet('vision.theme', theme) }, [theme])
  useEffect(() => { storageSet('vision.sidebarCollapsed', collapsed ? '1' : '0') }, [collapsed])
  useEffect(() => { storageSet('vision.projectFilter', projectFilter) }, [projectFilter])
  useEffect(() => { storageSetJson('vision.collapsedProjects', collapsedProjects) }, [collapsedProjects])
  useEffect(() => { messagesRef.current = messages }, [messages])
  useEffect(() => { draftRef.current = draft }, [draft])
  useEffect(() => { ttsPrefsRef.current = ttsPrefs; storageSetJson('vision.tts', ttsPrefs) }, [ttsPrefs])
  useEffect(() => { storageSet('vision.reduceMotion', reduceMotion ? '1' : '0') }, [reduceMotion])
  useEffect(() => voiceEngine.subscribe((key) => { speakingKeyRef.current = key; setSpeakingKey(key) }), [])
  useEffect(() => () => voiceEngine.stop(), [])
  // Switching chats silences any in-flight read-aloud from the previous one.
  useEffect(() => { voiceEngine.stop() }, [currentId])
  useEffect(() => {
    let alive = true
    loadVoices().then((voices) => { if (alive) setTtsVoices(voices) })
    const refresh = () => setTtsVoices(listVoices())
    try { window.speechSynthesis?.addEventListener('voiceschanged', refresh) } catch { /* voice list stays as-is */ }
    return () => { alive = false; try { window.speechSynthesis?.removeEventListener('voiceschanged', refresh) } catch { /* noop */ } }
  }, [])
  useEffect(() => {
    let mq
    try { mq = window.matchMedia('(max-width: 900px)') } catch { return undefined }
    const update = () => setNarrow(mq.matches)
    update()
    if (mq.addEventListener) { mq.addEventListener('change', update); return () => mq.removeEventListener('change', update) }
    mq.addListener?.(update)
    return () => mq.removeListener?.(update)
  }, [])

  const pushToast = useCallback((message, kind = 'info') => {
    if (!message) return
    const id = uid()
    setToasts((items) => [...items.slice(-2), { id, message: String(message), kind }])
    window.setTimeout(() => setToasts((items) => items.filter((item) => item.id !== id)), 3200)
  }, [])

  const callApi = useCallback(async (method, ...args) => {
    if (!nativeRef.current) return null
    const result = await bridgeApi(method, ...args)
    if (result && result.ok === false && result.message && !result.cancelled) pushToast(result.message, 'error')
    if (result && result.accepted === false && result.message) pushToast(result.message, 'error')
    return result
  }, [pushToast])

  const refreshLiveCapabilities = useCallback(async () => {
    if (!nativeRef.current) {
      setLiveCapabilities(normalizeLiveCapabilities(null, false))
      return null
    }
    const result = await callOptional('get_live_capabilities', null)
    if (result?.live) setLiveCapabilities(normalizeLiveCapabilities(result.live, true))
    return result
  }, [])

  const openLive = useCallback(() => {
    setLiveOpen(true)
    refreshLiveCapabilities()
  }, [refreshLiveCapabilities])

  const openExtEditor = useCallback(async (kind, item = null) => {
    if (item) {
      const config = await callApi('get_extension_config', kind, item.id)
      if (!config?.ok) { pushToast(config?.message || 'Could not load settings.', 'error'); return }
      setExtEditor({ kind, isNew: false, discovered: item.discovered_tools || [], values: { ...config, secret: '', auth_header: '', clear_secret: false } })
    } else {
      setExtEditor({ kind, isNew: true, discovered: [], values: { id: '', display_name: '', transport: kind === 'connector' ? 'streamable_http' : 'stdio', url: '', command: '', args: '', enabled: true, allow_sampling: false, allow_elicitation: true, timeout_seconds: 60, permissions: {}, auth_type: 'none', secret: '', auth_header: '', secret_env_name: 'API_KEY', auto_approve: false, allow_paths: '', cwd: '' } })
    }
  }, [callApi, pushToast])
  const saveExtEditor = useCallback(async () => {
    if (!extEditor) return
    const payload = { ...extEditor.values }
    delete payload.ok; delete payload.kind; delete payload.env_secret_names; delete payload.header_secret_names; delete payload.plain_env_names
    if (!payload.secret) delete payload.secret
    const result = await callApi('save_extension', extEditor.kind, payload)
    if (result?.ok) {
      if (result.extensions) setExtensions(result.extensions)
      setExtEditor(null)
      pushToast('Saved. Use Check on the card to review and trust its tools.', 'success')
    } else pushToast(result?.message || 'Could not save the extension.', 'error')
  }, [callApi, extEditor, pushToast])
  const toggleExtensionEnabled = useCallback(async (kind, item, enabled) => {
    const result = await callApi('save_extension', kind, { id: item.id, enabled })
    if (result?.ok && result.extensions) setExtensions(result.extensions)
    if (!result?.ok) pushToast(result?.message || 'Could not update.', 'error')
  }, [callApi, pushToast])
  const deleteExtension = useCallback(async (kind, item) => {
    const key = `${kind}:${item.id}`
    if (extDeleteArmed !== key) { setExtDeleteArmed(key); return }
    setExtDeleteArmed('')
    const result = kind === 'connector' ? await callApi('disconnect_connector', item.id) : await callApi('delete_extension', kind, item.id)
    if (result?.ok && result.extensions) setExtensions(result.extensions)
    pushToast(result?.ok ? 'Deleted.' : (result?.message || 'Could not delete.'), result?.ok ? 'success' : 'error')
  }, [callApi, extDeleteArmed, pushToast])

  const scrollBottom = useCallback((force = false) => {
    const node = transcriptRef.current
    if (!node || (!force && !stickRef.current)) return
    if (scrollFrameRef.current) return
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = 0
      node.scrollTop = node.scrollHeight
      setShowJump(false)
      // One delayed pass covers images/charts finishing layout after a
      // conversation switch, without scheduling work for every stream delta.
      if (force) window.setTimeout(() => { node.scrollTop = node.scrollHeight }, 90)
    })
  }, [])

  useEffect(() => { if (stickRef.current) scrollBottom() }, [messages, scrollBottom])

  // Optional hands-free listening (Settings -> Voice, off by default):
  // when a turn finishes, read the final answer once.
  useEffect(() => {
    const was = prevStreamingRef.current
    prevStreamingRef.current = streaming
    if (!was || streaming || !ttsPrefsRef.current.tts_auto_read) return
    const last = [...messagesRef.current].reverse().find((m) => m.role === 'assistant' && !m.streaming && !m.incomplete)
    if (!last?.content || autoSpokenKeyRef.current === last.key) return
    if (liveSpeechRef.current.key === last.key && liveSpeechRef.current.muted) { autoSpokenKeyRef.current = last.key; return }
    autoSpokenKeyRef.current = last.key
    voiceEngine.speak(extractThink(last.content).content || last.content, ttsPrefsRef.current, last.key)
  }, [streaming])

  const updateAssistant = useCallback((updater) => {
    const key = assistantKeyRef.current
    setMessages((items) => {
      let index = items.findIndex((item) => item.key === key)
      const next = [...items]
      if (index < 0) {
        const fresh = { key: key || uid(), role: 'assistant', content: '', reasoning: '', tools: [], sources: [], streaming: true }
        assistantKeyRef.current = fresh.key
        next.push(fresh); index = next.length - 1
      }
      next[index] = updater({ ...next[index], tools: [...(next[index].tools || [])] })
      return next
    })
  }, [])

  const enqueueConfirm = useCallback((item) => setConfirmQueue((items) => [...items, item]), [])
  const localConfirm = useCallback((title, message, details, options = {}) => new Promise((resolve) => {
    enqueueConfirm({ kind: 'local', title, message, details, resolve, approveLabel: options.approveLabel || 'Allow', denyLabel: options.denyLabel || 'Deny' })
  }), [enqueueConfirm])

  const resolveConfirm = useCallback(async (approved) => {
    const item = confirmQueue[0]
    if (!item) return
    setConfirmQueue((items) => items.slice(1))
    if (item.kind === 'native') await callOptional('resolve_confirmation', { ok: false }, item.id, Boolean(approved), item.details?.elicitation_fields ? elicitValues : null)
    else item.resolve?.(Boolean(approved))
  }, [confirmQueue, elicitValues])

  const askPrompt = useCallback(({ title, label, value = '', confirmLabel = 'Save', rows = 5 }) => new Promise((resolve) => {
    setPromptValue(value)
    setPromptState({ title, label, confirmLabel, rows, resolve })
  }), [])
  const closePrompt = useCallback((value) => {
    promptState?.resolve?.(value)
    setPromptState(null)
  }, [promptState])

  const loadConnectorDirectory = useCallback(async () => {
    const result = await callApi('get_connector_directory')
    if (Array.isArray(result?.directory)) setConnectorDirectory(result.directory)
  }, [callApi])
  const installDirectoryConnector = useCallback(async (entry) => {
    let secret = ''
    if (entry.auth_type === 'api_key') {
      secret = await askPrompt({ title: `Connect ${entry.name}`, label: 'API key (stored in Windows Credential Manager, never shown again)', confirmLabel: 'Connect', rows: 2 })
      if (!secret) return
    }
    if (entry.auth_type === 'oauth' && !entry.oauth_ready) { pushToast("This directory entry doesn't include sign-in endpoints yet, so it can't be installed.", 'error'); return }
    pushToast(entry.auth_type === 'oauth' ? 'Opening your browser to sign in…' : `Connecting ${entry.name}…`)
    const result = await callApi('install_connector', entry.id, secret || '')
    if (result && result.accepted === false) pushToast(result.message || 'Install already running.', 'error')
    window.setTimeout(loadConnectorDirectory, 1500)
  }, [askPrompt, callApi, loadConnectorDirectory, pushToast])
  const previewSkill = useCallback(async (name) => {
    const result = await callApi('get_skill_preview', name)
    if (result?.ok) setSkillPreview(result)
    else pushToast(result?.message || 'Could not preview the skill.', 'error')
  }, [callApi, pushToast])

  const applySettingsData = useCallback((data = {}) => {
    if (data.app) setAppInfo(data.app)
    if (data.config) { setConfig((c) => ({ ...c, ...data.config })); if (data.config.theme) setTheme(data.config.theme); if (data.config.gpu_layers != null) setSetupGpu(data.config.gpu_layers); if (data.config.tts_style != null || data.config.tts_voice_uri != null) setTtsPrefs(normalizeTts(data.config)); if (data.config.reduce_motion != null) setReduceMotion(Boolean(data.config.reduce_motion)) }
    if (data.credentials) setCredentials(data.credentials)
    if (data.paths) setPaths((p) => ({ ...p, ...data.paths }))
    if (data.projects) { setProjects(data.projects); projectsLoadedRef.current = true }
    if (data.skills) setSkills(data.skills)
    if (data.extensions) setExtensions(data.extensions)
    if (data.server) setServer((s) => ({ ...s, ...data.server }))
    if (data.updates) setUpdates((current) => ({ ...current, ...data.updates, channels: { ...(current?.channels || {}), ...(data.updates.channels || {}) } }))
    if (data.telemetry) setTelemetry((current) => ({ ...current, ...data.telemetry }))
    if (data.available_models) setAvailableModels(data.available_models)
    if (data.hardware) setHardware(data.hardware)
    if (data.live) setLiveCapabilities(normalizeLiveCapabilities(data.live, nativeRef.current))
  }, [])

  const openSettings = useCallback(async (tab = 'general') => {
    const normalizedTab = { endpoint: 'model', connections: 'model', extensions: 'tools', speech: 'voice', audio: 'voice', sound: 'voice' }[tab] || tab
    setSettingsTab(normalizedTab); setOverlay('settings')
    let data = null
    if (nativeRef.current) data = await callApi('get_settings')
    if (data) applySettingsData(data)
    const c = { ...config, ...(data?.config || {}) }
    const skillList = data?.skills || skills
    setForm({
      theme: c.theme || theme, endpoint_mode: c.endpoint_mode || 'auto', active_project_id: c.active_project_id || '',
      max_extension_tools: c.max_extension_tools ?? 32, auto_start_local_server: Boolean(c.auto_start_local_server),
      model_path: c.model_path || data?.paths?.model_file || paths.model_file || '', llama_server_path: c.llama_server_path || '',
      local_port: c.local_port ?? 8080, context_size: c.context_size ?? 16384, gpu_layers: c.gpu_layers ?? 99, performance_profile: c.performance_profile || 'auto',
      local_model_override: c.local_model_override || '', remote_base_url: '', remote_base_url_configured: Boolean(c.remote_base_url_configured), clear_remote_base_url: false, remote_model_override: c.remote_model_override || '',
      telemetry_opt_in: Boolean(c.telemetry_opt_in), updates_auto_check: c.updates_auto_check !== false, update_channel: c.update_channel || 'stable', enabled_skills: skillList.filter((s) => s.enabled).map((s) => s.name),
      stt_model_size: c.stt_model_size || 'base', stt_language: c.stt_language || 'auto', mmproj_path: c.mmproj_path || '',
      remote_api_key: '', clear_remote_api_key: false, hf_token: '', clear_hf_token: false,
      // Fall back to the live prefs so preview/gallery mode (no backend
      // config) still shows and edits the real current voice settings.
      ...normalizeTts({ ...ttsPrefsRef.current, ...Object.fromEntries(Object.entries(c).filter(([k, v]) => k.startsWith('tts_') && v != null)) }),
      reduce_motion: c.reduce_motion != null ? Boolean(c.reduce_motion) : storageGet('vision.reduceMotion') === '1',
    })
    loadVoices().then((voices) => setTtsVoices(voices))
  }, [applySettingsData, callApi, config, paths.model_file, skills, theme])

  const refreshUpdates = useCallback(async () => {
    if (!nativeRef.current) { setUpdates(demoUpdates); return }
    const result = await callOptional('get_updates_state', null)
    if (result?.updates) setUpdates(result.updates)
  }, [])

  const refreshTelemetry = useCallback(async () => {
    if (!nativeRef.current) return
    const result = await callOptional('get_telemetry_status', null)
    if (result?.telemetry) setTelemetry(result.telemetry)
  }, [])

  const refreshCheckpoints = useCallback(async () => {
    if (!nativeRef.current) { setCodeCheckpoints([]); return }
    const result = await callOptional('list_checkpoints', null)
    if (result?.checkpoints) setCodeCheckpoints(result.checkpoints)
  }, [])

  const openTelemetryPreview = useCallback(async () => {
    if (!nativeRef.current) {
      setTelemetryPreview({
        needs_jsonl: '{"signal":"user_negative","detail":{"target":"Example correction"}}\n',
        report: { week: 'Preview', total_events: 1, by_category: { general: 1 }, by_signal: { user_negative: 1 }, fix_rate: 1, top_failing_tools: [] },
        event_count: 1,
      })
      return
    }
    const result = await callApi('preview_telemetry')
    if (result) {
      if (result.telemetry) setTelemetry(result.telemetry)
      setTelemetryPreview(result)
    }
  }, [callApi])

  const clearLocalTelemetry = useCallback(async () => {
    if (!telemetryClearArmed) { setTelemetryClearArmed(true); return }
    setTelemetryClearArmed(false)
    if (!nativeRef.current) {
      setTelemetry((current) => ({ ...current, pending_count: 0, status: { ...(current.status || {}), pending_events: 0 }, report: null }))
      setTelemetryPreview(null)
      return
    }
    const result = await callApi('clear_feedback')
    if (result?.telemetry) setTelemetry(result.telemetry)
    setTelemetryPreview(null)
    if (result?.ok) pushToast('Local telemetry cleared.', 'success')
  }, [callApi, pushToast, telemetryClearArmed])

  const checkUpdateChannel = useCallback(async (channel = 'all') => {
    if (!nativeRef.current) { pushToast('Update checks run in the desktop app.', 'error'); return }
    await callApi('check_updates', { channel })
  }, [callApi, pushToast])

  const downloadUpdateChannel = useCallback(async (channel, version = '') => {
    if (!nativeRef.current) { pushToast('Update downloads run in the desktop app.', 'error'); return }
    if (channel === 'app') {
      const approved = await localConfirm(
        'Download this VISION update?',
        'VISION will download and verify the update in the background. The update will not be applied until you choose Restart to apply.',
        {},
        { approveLabel: 'Download update', denyLabel: 'Not now' },
      )
      if (!approved) return
    }
    await callApi('download_update', { channel, version })
  }, [callApi, localConfirm, pushToast])

  const restartToApplyUpdate = useCallback(async () => {
    if (!nativeRef.current) { pushToast('Restart-to-apply runs in the desktop app.', 'error'); return }
    const approved = await localConfirm(
      'Restart VISION now?',
      'The downloaded update will be applied by the VISION launcher after this window closes.',
      {},
      { approveLabel: 'Restart now', denyLabel: 'Later' },
    )
    if (!approved) return
    const result = await callApi('restart_to_apply_update')
    if (result?.updates) setUpdates(result.updates)
  }, [callApi, localConfirm, pushToast])

  const rollbackUpdate = useCallback(async () => {
    if (!nativeRef.current) { pushToast('Rollback runs in the desktop app.', 'error'); return }
    const approved = await localConfirm(
      'Roll back VISION?',
      'The previous installed version will be staged and used after VISION restarts.',
      {},
      { approveLabel: 'Stage rollback', denyLabel: 'Cancel' },
    )
    if (!approved) return
    await callApi('rollback_update', { channel: 'app' })
  }, [callApi, localConfirm, pushToast])

  const changeUpdatePreferences = useCallback(async (partial) => {
    setUpdates((current) => ({ ...current, ...partial }))
    setForm((current) => current ? {
      ...current,
      ...(partial.auto_check !== undefined ? { updates_auto_check: Boolean(partial.auto_check) } : {}),
      ...(partial.channel !== undefined ? { update_channel: partial.channel } : {}),
    } : current)
    if (!nativeRef.current) return
    const result = await callApi('set_update_preferences', partial)
    if (result?.updates) setUpdates(result.updates)
    if (result?.config) setConfig((current) => ({ ...current, ...result.config }))
  }, [callApi])

  const openServer = useCallback(async () => {
    setOverlay('server')
    if (nativeRef.current) {
      const result = await callApi('get_server_state')
      const payload = result?.server || result
      if (payload) { setServer((s) => ({ ...s, ...payload })); if (Array.isArray(payload.logs)) setServerLogs(payload.logs.slice(-300)) }
    }
  }, [callApi])

  const openActivity = useCallback(async () => {
    setOverlay('activity'); setClearArmed(false)
    if (nativeRef.current) { const data = await callApi('get_activity'); if (data) { setActivity(data); if (data.telemetry) setTelemetry(data.telemetry) } }
    else setActivity({ audit_text: 'Preview mode has no local audit log.', feedback_rows: [], feedback_count: 0 })
  }, [callApi])

  const selectConversation = useCallback(async (id, force = false) => {
    if (!id || (streamingRef.current && !force)) { if (streamingRef.current) pushToast('Stop the current response before switching chats.', 'error'); return }
    if (!nativeRef.current) {
      setCurrentId(String(id)); setCurrentConversation(conversations.find((c) => String(c.id) === String(id)) || null)
      setMessages(String(id) === 'demo-1' ? demoMessages : []); setSources([]); sticksToBottom(); return
    }
    const result = await callApi('get_conversation', id)
    if (!result) return
    setCurrentId(String(id)); setCurrentConversation(result.conversation || null)
    if (result.conversation?.project_id !== undefined) setSelectedProject(String(result.conversation.project_id || ''))
    setMessages(normalizeMessages(result.messages || [])); setSources([])
    sticksToBottom()
    function sticksToBottom() { stickRef.current = true; window.setTimeout(() => scrollBottom(true), 30) }
  }, [callApi, conversations, pushToast, scrollBottom])

  const newChat = useCallback(async (projectOverride) => {
    if (streamingRef.current) { pushToast('Stop the current response before starting another chat.', 'error'); return }
    const targetProject = typeof projectOverride === 'string'
      ? projectOverride
      : projectFilter === 'all' ? selectedProject : projectFilter === 'none' ? '' : projectFilter
    if (currentIdRef.current && messages.length === 0 && String(currentConversation?.project_id || '') === String(targetProject || '')) {
      setDraft(''); setSources([]); window.setTimeout(() => inputRef.current?.focus(), 30); return
    }
    if (!nativeRef.current) {
      const id = `local-${uid()}`
      const row = { id, title: 'New chat', pinned: false, project_id: targetProject || '', updated_at: Date.now() / 1000 }
      setConversations((items) => [row, ...items])
      setCurrentId(id); setCurrentConversation(row); setSelectedProject(targetProject || ''); setMessages([]); setSources([]); return
    }
    const result = await callApi('create_conversation', targetProject || '')
    if (!result) return
    if (result.conversations) setConversations(result.conversations)
    setCurrentConversation(result.conversation || null); setCurrentId(String(result.conversation?.id || ''))
    setSelectedProject(String(result.conversation?.project_id || targetProject || '')); setMessages([]); setSources([])
  }, [callApi, currentConversation?.project_id, messages.length, projectFilter, pushToast, selectedProject])

  const stopGeneration = useCallback(async () => {
    simTimersRef.current.forEach((timer) => window.clearTimeout(timer)); simTimersRef.current = []
    if (nativeRef.current) await callApi('stop_generation')
    if (!nativeRef.current) { setStreaming(false); updateAssistant((m) => ({ ...m, streaming: false, incomplete: true, content: m.content || 'Generation stopped.' })) }
  }, [callApi, updateAssistant])

  const simulatePreview = useCallback((text, firstExchange = false) => {
    const lower = text.toLowerCase()
    const after = (ms, fn) => { const id = window.setTimeout(fn, ms); simTimersRef.current.push(id) }
    after(250, () => updateAssistant((m) => ({ ...m, reasoning: 'Planning the response and choosing the right local tool.' })))
    let final = 'This is a browser preview, so I am simulating the interface without the Python bridge. Launch VISION Desktop to use the real local model and tools.'
    if (/chart|draw|graph|plot/.test(lower)) {
      after(500, () => updateAssistant((m) => ({ ...m, tools: [...m.tools, { id: 'sim-chart', name: 'ui_chart', args: { title: 'Squares', chart_type: 'bar', labels: ['1', '2', '3', '4'], values: [1, 4, 9, 16] }, status: 'running', startedAt: Date.now() }] })))
      after(1050, () => updateAssistant((m) => ({ ...m, tools: m.tools.map((t) => t.id === 'sim-chart' ? { ...t, status: 'success', durationMs: 48, summary: 'Rendered chart.', genui: { kind: 'chart', title: 'Squares', chart_type: 'bar', labels: ['1', '2', '3', '4'], values: [1, 4, 9, 16] } } : t) })))
      final = 'Here is the chart. Each step squares the input, so the bars separate quickly.'
    } else if (/system|cpu|ram|status/.test(lower)) {
      after(500, () => updateAssistant((m) => ({ ...m, tools: [...m.tools, { id: 'sim-sys', name: 'system_status', args: {}, status: 'success', durationMs: 164, summary: 'System status collected.', raw: demoMessages[1].tools[0].raw }] })))
      final = 'The machine looks healthy: CPU and memory are comfortably below saturation, with plenty of disk free.'
    } else if (/weather/.test(lower)) {
      after(500, () => updateAssistant((m) => ({ ...m, tools: [...m.tools, { id: 'sim-weather', name: 'get_weather', args: { city: 'Coimbatore' }, status: 'success', durationMs: 620, summary: 'Live weather fetched.', raw: { city: 'Coimbatore', temp_c: 31, humidity: 54 } }] })))
      final = 'Coimbatore is around 31°C with moderate humidity in this preview simulation.'
    }
    const words = final.split(' ')
    words.forEach((word, i) => after(1150 + i * 35, () => updateAssistant((m) => ({ ...m, content: `${m.content}${i ? ' ' : ''}${word}` }))))
    after(1250 + words.length * 35, () => {
      setStreaming(false)
      updateAssistant((m) => ({ ...m, streaming: false }))
      if (firstExchange) {
        const title = autoConversationTitle(text, final)
        setConversations((items) => items.map((item) => (
          String(item.id) === String(currentIdRef.current) && (!item.title || item.title === 'New chat')
            ? { ...item, title }
            : item
        )))
        setCurrentConversation((item) => (
          item && String(item.id) === String(currentIdRef.current) && (!item.title || item.title === 'New chat')
            ? { ...item, title }
            : item
        ))
      }
    })
  }, [updateAssistant])

  const sendText = useCallback(async (raw, options = {}) => {
    const text = String(raw || '').trim()
    if (!text || streamingRef.current) return null
    const command = text.toLowerCase()
    // UI slash shortcuts are for typed composer input. A spoken transcript
    // is always treated as a message (backend /fix and /teach still work).
    if (!options.fromLive) {
      if (command === '/settings') { setDraft(''); openSettings(); return null }
      if (command === '/server') { setDraft(''); openServer(); return null }
      if (command === '/activity') { setDraft(''); openActivity(); return null }
      if (command === '/new') { setDraft(''); newChat(); return null }
      if (command === '/help') { setDraft(''); setOverlay('palette'); return null }
      if (command === '/agent') { setDraft(''); toggleAgentMode(!agentModeRef.current); pushToast(agentModeRef.current ? 'Agent mode on — VISION will plan and work step by step.' : 'Agent mode off.'); return null }
    }

    const firstExchange = !messagesRef.current.some((item) => item.role === 'user')
    const assistantKey = uid(); assistantKeyRef.current = assistantKey
    setMessages((items) => [...items,
      { key: uid(), role: 'user', content: text, tools: [] },
      { key: assistantKey, role: 'assistant', content: '', reasoning: '', tools: [], sources: [], streaming: true }])
    setSources([]); setDraft(''); setStreaming(true); stickRef.current = true
    if (!nativeRef.current) { simulatePreview(text, firstExchange); setAttachments([]); return assistantKey }
    const attachmentIds = attachments.map((item) => item.id)
    const payload = { text, conversation_id: currentIdRef.current || '', project_id: selectedProject || '', attachment_ids: attachmentIds, agent_mode: agentModeRef.current }
    if (options.imageDataUrl) {
      payload.live_image_data_url = options.imageDataUrl
      payload.live_screen_approved = true
    }
    const result = await callApi('send_message', payload)
    if (!result?.accepted) {
      setStreaming(false)
      updateAssistant((m) => ({ ...m, streaming: false, incomplete: true, content: result?.message || 'The message could not be started.' }))
      return null
    }
    setAttachments([])
    return assistantKey
  }, [attachments, callApi, newChat, openActivity, openServer, openSettings, selectedProject, simulatePreview, updateAssistant])

  const sendLiveText = useCallback(
    (text, liveOptions = {}) => sendText(text, { ...liveOptions, fromLive: true }),
    [sendText],
  )

  const regenerate = useCallback(async () => {
    if (!nativeRef.current || streamingRef.current || !currentIdRef.current) return
    setMessages((items) => {
      let lastUser = -1
      items.forEach((item, i) => { if (item.role === 'user') lastUser = i })
      return lastUser >= 0 ? items.slice(0, lastUser + 1) : items
    })
    const key = uid(); assistantKeyRef.current = key
    setMessages((items) => [...items, { key, role: 'assistant', content: '', reasoning: '', tools: [], sources: [], streaming: true }])
    setStreaming(true)
    const result = await callApi('regenerate', currentIdRef.current)
    if (!result?.accepted) { setStreaming(false); updateAssistant((m) => ({ ...m, streaming: false, incomplete: true, content: result?.message || 'Could not regenerate the last response.' })) }
  }, [callApi, updateAssistant])

  const copyText = useCallback(async (text) => {
    const result = await callOptional('copy_text', null, String(text || ''))
    if (!result?.ok) {
      try { await navigator.clipboard.writeText(String(text || '')) } catch { pushToast('Could not copy text.', 'error'); return }
    }
    pushToast('Copied.', 'success')
  }, [pushToast])

  const chooseAttachments = useCallback(async () => {
    if (!nativeRef.current) { pushToast('Attachments require the desktop shell.', 'error'); return }
    const result = await callApi('choose_attachments')
    if (result?.attachments) setAttachments(result.attachments)
    if (Array.isArray(result?.items)) { setLibraryItems(result.items); setLibraryCount(result.library_count ?? result.items.length) }
    if (result?.message) pushToast(result.message)
  }, [callApi, pushToast])

  const removeAttachment = useCallback(async (item) => {
    const result = await callApi('remove_attachment', item.id)
    if (result?.attachments) setAttachments(result.attachments)
    else setAttachments((items) => items.filter((x) => x.id !== item.id))
  }, [callApi])

  const toggleTheme = useCallback(async () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    setTheme(next)
    if (nativeRef.current) await callApi('save_settings', { theme: next })
  }, [callApi, theme])

  const refreshLibrary = useCallback(async () => {
    setLibraryLoading(true)
    try {
      if (!nativeRef.current) {
        setLibraryItems((items) => items.length ? items : demoLibrary)
        setLibraryCount((count) => count || demoLibrary.length)
        return
      }
      const result = await callApi('get_library')
      if (Array.isArray(result?.items)) {
        setLibraryItems(result.items)
        setLibraryCount(result.library_count ?? result.items.length)
      }
    } finally { setLibraryLoading(false) }
  }, [callApi])

  const openLibrary = useCallback(() => {
    setLibraryOpen(true)
    refreshLibrary()
  }, [refreshLibrary])

  const closeLibrary = useCallback(() => setLibraryOpen(false), [])

  const importLibraryFiles = useCallback(async () => {
    if (!nativeRef.current) { pushToast('Uploads require the desktop shell.', 'error'); return }
    const result = await callApi('import_library_files')
    if (Array.isArray(result?.items)) { setLibraryItems(result.items); setLibraryCount(result.library_count ?? result.items.length) }
    if (result?.message) pushToast(result.message)
    else if (result?.imported_ids?.length) pushToast(`Added ${result.imported_ids.length} file(s) to the Item Library.`, 'success')
  }, [callApi, pushToast])

  const attachLibraryItem = useCallback(async (item) => {
    if (!item?.attachable) return
    if (!nativeRef.current) {
      setAttachments((items) => {
        if (items.some((x) => String(x.library_item_id || '') === String(item.id))) return items
        return [...items, { id: `library-${item.id}`, library_item_id: item.id, name: item.name, size: item.size, kind: item.kind, extension: item.extension }]
      })
    } else {
      const result = await callApi('attach_library_item', item.id)
      if (result?.attachments) setAttachments(result.attachments)
      if (result?.message) pushToast(result.message, result.ok === false ? 'error' : 'info')
    }
    setLibraryOpen(false)
    window.setTimeout(() => inputRef.current?.focus(), 40)
  }, [callApi, pushToast])

  const renameLibraryItem = useCallback(async (itemId, name) => {
    const clean = String(name || '').trim()
    if (!clean) return
    if (!nativeRef.current) {
      setLibraryItems((items) => items.map((item) => String(item.id) === String(itemId) ? { ...item, name: clean } : item))
      setAttachments((items) => items.map((item) => String(item.library_item_id || '') === String(itemId) ? { ...item, name: clean } : item))
      return
    }
    const result = await callApi('rename_library_item', itemId, clean)
    if (Array.isArray(result?.items)) { setLibraryItems(result.items); setLibraryCount(result.library_count ?? result.items.length) }
    if (result?.attachments) setAttachments(result.attachments)
  }, [callApi])

  const deleteLibraryItem = useCallback(async (item) => {
    const approved = await localConfirm(
      'Delete this library file?',
      'The managed copy will be removed from the Item Library. Your original file outside VISION will not be touched.',
      { name: item.name || 'Untitled file', size: formatBytes(item.size) },
      { approveLabel: 'Delete file', denyLabel: 'Cancel' },
    )
    if (!approved) return
    if (!nativeRef.current) {
      setLibraryItems((items) => items.filter((x) => String(x.id) !== String(item.id)))
      setLibraryCount((count) => Math.max(0, count - 1))
      setAttachments((items) => items.filter((x) => String(x.library_item_id || '') !== String(item.id)))
      return
    }
    const result = await callApi('delete_library_item', item.id)
    if (Array.isArray(result?.items)) { setLibraryItems(result.items); setLibraryCount(result.library_count ?? result.items.length) }
    if (result?.attachments) setAttachments(result.attachments)
    if (result?.ok) pushToast('Removed from the Item Library.', 'success')
  }, [callApi, localConfirm, pushToast])

  const renameConversationById = useCallback(async (conversationId, title) => {
    const clean = String(title || '').trim()
    if (!clean) return
    if (!nativeRef.current) {
      setConversations((items) => items.map((item) => String(item.id) === String(conversationId) ? { ...item, title: clean } : item))
    } else {
      const result = await callApi('rename_conversation', conversationId, clean)
      if (result?.conversations) setConversations(result.conversations)
      if (result?.conversation && String(result.conversation.id) === String(currentIdRef.current)) setCurrentConversation(result.conversation)
    }
    setCurrentConversation((item) => item && String(item.id) === String(conversationId) ? { ...item, title: clean } : item)
  }, [callApi])

  const toggleConversationPinned = useCallback(async (row) => {
    const latest = conversations.find((item) => String(item.id) === String(row.id)) || row
    const next = !latest.pinned
    if (!nativeRef.current) {
      setConversations((items) => items.map((item) => String(item.id) === String(row.id) ? { ...item, pinned: next } : item))
      return
    }
    const result = await callApi('set_conversation_pinned', row.id, next)
    if (result?.conversations) setConversations(result.conversations)
  }, [callApi, conversations])

  const assignConversationToProject = useCallback(async (row, projectId) => {
    const cleanProjectId = String(projectId || '')
    if (!nativeRef.current) {
      setConversations((items) => items.map((item) => String(item.id) === String(row.id) ? { ...item, project_id: cleanProjectId } : item))
    } else {
      const result = await callApi('assign_conversation_project', row.id, cleanProjectId)
      if (result?.conversations) setConversations(result.conversations)
      if (result?.projects) setProjects(result.projects)
      if (result?.conversation && String(result.conversation.id) === String(currentIdRef.current)) setCurrentConversation(result.conversation)
    }
    if (String(row.id) === String(currentIdRef.current)) {
      setSelectedProject(cleanProjectId)
      setCurrentConversation((item) => item ? { ...item, project_id: cleanProjectId } : item)
    }
  }, [callApi])

  const deleteConversationById = useCallback(async (menuRow) => {
    const row = conversations.find((item) => String(item.id) === String(menuRow.id)) || menuRow
    const pinned = Boolean(row.pinned)
    const approved = await localConfirm(
      pinned ? 'Unpin and delete this pinned chat?' : 'Delete this chat?',
      pinned
        ? 'Pinned chats are protected by an extra confirmation. Choose Unpin & delete only if you really want this conversation and its messages permanently removed.'
        : 'The conversation and its messages will be permanently removed from local history.',
      { title: row.title || 'New chat', pinned },
      { approveLabel: pinned ? 'Unpin & delete' : 'Delete', denyLabel: 'Cancel' },
    )
    if (!approved) return
    if (!nativeRef.current) {
      setConversations((items) => {
        const next = items.filter((item) => String(item.id) !== String(row.id))
        if (String(row.id) === String(currentIdRef.current)) {
          const fallback = next[0]
          window.setTimeout(() => { if (fallback) selectConversation(fallback.id, true) }, 0)
        }
        return next
      })
      return
    }
    const result = await callApi('delete_conversation', row.id)
    if (result?.conversations) {
      setConversations(result.conversations)
      const next = result.current_conversation_id || result.conversations[0]?.id
      if (next && String(row.id) === String(currentIdRef.current)) selectConversation(next, true)
    }
  }, [callApi, conversations, localConfirm, selectConversation])

  const exportConversationById = useCallback(async (row) => {
    if (!row?.id) return
    if (!nativeRef.current) { pushToast('Conversation export runs in the desktop app.', 'error'); return }
    await callApi('export_conversation', row.id)
  }, [callApi, pushToast])

  const createProject = useCallback(async () => {
    const name = await askPrompt({ title: 'Create project', label: 'Project name', value: '', confirmLabel: 'Create project', rows: 1 })
    const clean = String(name || '').trim()
    if (!clean) return
    if (!nativeRef.current) {
      const project = { id: `local-project-${uid()}`, name: clean }
      setProjects((items) => [...items, project])
      setProjectFilter(project.id)
      return
    }
    const result = await callApi('create_project', clean)
    if (result?.projects) setProjects(result.projects)
    if (result?.project?.id) setProjectFilter(String(result.project.id))
    if (result?.ok) pushToast('Project created.', 'success')
  }, [askPrompt, callApi, pushToast])

  const handleProjectFilterChange = useCallback((value) => {
    const next = String(value || 'all')
    setProjectFilter(next)
    if (next !== 'all' && next !== 'none') {
      setSelectedProject(next)
      setCollapsedProjects((items) => ({ ...items, [next]: false }))
    }
  }, [])

  const toggleProjectGroup = useCallback((projectId) => {
    const key = String(projectId)
    setCollapsedProjects((items) => ({ ...items, [key]: !items[key] }))
  }, [])

  // In a narrow window the rail is forced; "toggle" there means "keep it
  // open when the window widens", not a flip that would surprise later.
  const toggleSidebar = useCallback(() => setCollapsed((value) => (narrow ? false : !value)), [narrow])

  const openProject = useCallback(async (projectId) => {
    const id = String(projectId || '')
    setCollapsed(false)
    handleProjectFilterChange(id || 'none')
    if (!nativeRef.current) return
    const result = await callApi('set_active_project', id, currentIdRef.current || '')
    if (result?.projects) setProjects(result.projects)
    if (result?.conversations) setConversations(result.conversations)
    if (result?.conversation && String(result.conversation.id) === String(currentIdRef.current)) setCurrentConversation(result.conversation)
  }, [callApi, handleProjectFilterChange])

  const handleComposerSubmit = useCallback(() => {
    if (streamingRef.current) stopGeneration()
    else {
      // Read the live textarea as a fallback for IME composition, while the
      // controlled draft remains the normal source of truth.
      sendText(draftRef.current || inputRef.current?.value || '')
    }
  }, [sendText, stopGeneration])

  const handleTaskFinished = useCallback(async (payload = {}) => {
    setTask(null)
    if (!payload.ok) { pushToast(payload.error || `${payload.name || 'Task'} failed`, 'error'); return }
    const result = payload.result || {}
    if (result.updates) setUpdates(result.updates)
    if (result.telemetry) setTelemetry(result.telemetry)
    if (result.live) setLiveCapabilities(normalizeLiveCapabilities(result.live, nativeRef.current))
    if (payload.name === 'upload_feedback' && result.ok === false) {
      pushToast(result.message || 'Feedback upload failed; the local queue was retained.', 'error')
      return
    }
    if (['check_updates', 'download_update', 'apply_update', 'rollback_update'].includes(String(payload.name || '')) && result.ok === false) {
      pushToast(result.message || 'The update action failed.', 'error')
      return
    }
    pushToast(result.message || 'Done', 'success')
    if (result.server) setServer((s) => ({ ...s, ...result.server }))
    if (result.config) setConfig((c) => ({ ...c, ...result.config }))
    if (result.skills) setSkills(result.skills)
    if (payload.name === 'attach_mcp_resource' && result.attachments) { setAttachments(result.attachments); pushToast(`Attached ${result.name || 'resource'} to your next message.`, 'success') }
    if (payload.name === 'get_mcp_prompt' && typeof result.text === 'string') { setDraft(result.text); setOverlay(''); pushToast('Prompt inserted — review it, then send.', 'success') }
    if (String(payload.name || '').startsWith('discover_extension:')) {
      const toolNames = (result.tools || []).map((tool) => tool.name || tool.title || '').filter(Boolean)
      const approved = await localConfirm(
        `Trust ${result.display_name || result.id}?`,
        'These exact tool definitions will be fingerprinted. If the server later changes a name, description, schema, or annotations, VISION will require trust again. Individual tools default to asking before execution.',
        { fingerprint: result.fingerprint, tools: result.tools || [] },
        { approveLabel: 'Trust toolset', denyLabel: 'Cancel' },
      )
      if (approved) { await callApi('trust_extension', result.kind, result.id, result.fingerprint, toolNames); pushToast('Extension toolset trusted. Refreshing tools…', 'success') }
    }
    if (payload.name === 'check_model_update') pushToast(result.status === 'current' ? 'The installed model is current.' : `Model update check: ${result.status || 'complete'}`, 'success')
  }, [callApi, localConfirm, pushToast])

  useEffect(() => {
    const offs = [
      on('content_delta', (p) => updateAssistant((m) => ({ ...m, content: `${m.content || ''}${p.text || ''}`, streaming: true }))),
      on('reasoning_delta', (p) => updateAssistant((m) => ({ ...m, reasoning: `${m.reasoning || ''}${p.text || ''}`, streaming: true }))),
      on('tool_started', (p) => updateAssistant((m) => ({ ...m, tools: [...m.tools, { id: String(p.tool_call_id || p.id || uid()), name: p.name || 'tool', args: parseArgs(p.arguments ?? p.args ?? {}), status: 'running', startedAt: Date.now() }] }))),
      on('tool_finished', (p) => updateAssistant((m) => {
        const id = String(p.tool_call_id || p.id || '')
        const found = m.tools.some((tool) => String(tool.id) === id)
        const done = { status: p.is_error || p.error_kind ? 'error' : 'success', durationMs: p.duration_ms, summary: p.summary, content: p.content, raw: p.raw, genui: p.genui, name: p.name || 'tool', args: parseArgs(p.arguments || {}) }
        return { ...m, tools: found ? m.tools.map((tool) => String(tool.id) === id ? { ...tool, ...done } : tool) : [...m.tools, { id, startedAt: Date.now(), ...done }] }
      })),
      on('endpoint', (p) => {
        const cloud = /remote|cloud/i.test(`${p.kind || ''} ${p.label || ''}`)
        setEndpoint({ cloud, kind: p.kind || '', label: p.label || '' })
        if (p.failed_over) setBanner('Local endpoint failed, so this request used the configured cloud endpoint.')
        else if (p.failover_offered) setBanner('Local endpoint is unavailable. Cloud failover is available in Auto mode.')
      }),
      on('sources', (p) => {
        const list = p.sources || p || []
        setSources(Array.isArray(list) ? list : [])
        updateAssistant((m) => ({ ...m, sources: Array.isArray(list) ? list : [] }))
      }),
      on('confirmation_requested', (p) => enqueueConfirm({ kind: 'native', id: p.id, title: p.title || 'Allow this action?', message: p.message || 'VISION is asking for approval before continuing.', details: p.details || {}, approveLabel: 'Allow', denyLabel: 'Deny' })),
      on('turn_finished', (p) => {
        if (p.conversation_id) setCurrentId(String(p.conversation_id))
        if (Array.isArray(p.conversations)) {
          setConversations(p.conversations)
          const refreshed = p.conversations.find((item) => String(item.id) === String(p.conversation_id || currentIdRef.current))
          if (refreshed) setCurrentConversation((item) => ({ ...(item || {}), ...refreshed }))
        }
        if (Array.isArray(p.sources) && p.sources.length) setSources(p.sources)
        updateAssistant((m) => {
          let content = p.assistant_text ?? m.content ?? ''
          if (p.error && !String(content).trim()) content = `Something went wrong:\n\n\`${String(p.error).replaceAll('`', "'")}\``
          if (p.cancelled && !String(content).trim()) content = 'Generation stopped.'
          return { ...m, content, reasoning: p.reasoning ?? m.reasoning ?? '', sources: p.sources?.length ? p.sources : m.sources, streaming: false, incomplete: Boolean(p.incomplete || p.error || p.cancelled) }
        })
        setStreaming(false)
        window.setTimeout(() => inputRef.current?.focus(), 30)
      }),
      on('server_state', (p) => setServer((s) => ({ ...s, ...(p.server || p) }))),
      on('server_log', (p) => setServerLogs((lines) => [...lines.slice(-299), String(p.line || '')])),
      on('banner', (p) => setBanner(p.message || '')),
      on('toast', (p) => pushToast(p.message || '', p.kind || 'info')),
      on('error', (p) => pushToast(p.message || 'Something went wrong', 'error')),
      on('task_started', (p) => setTask({ name: p.name, message: 'Working…', percent: null })),
      on('task_progress', (p) => setTask(p)),
      on('task_finished', (p) => { handleTaskFinished(p) }),
      on('extensions_updated', (p) => setExtensions(p)),
      on('extension_event', (p) => { if (p.kind === 'list_changed') pushToast('A connected tool changed its tool list — refreshing. Changed tools need re-approval.', 'success') }),
      on('skill_used', (p) => updateAssistant((m) => ({ ...m, skills: [...new Set([...(m.skills || []), ...(p.names || [])])] }))),
      on('routing_updated', (p) => { if (p.routing) setExtensions((e) => ({ ...e, routing: p.routing })) }),
      on('updates_updated', (p) => { if (p.updates) setUpdates(p.updates) }),
      on('live_updated', (p) => { if (p.live) setLiveCapabilities(normalizeLiveCapabilities(p.live, true)) }),
      on('update_progress', (p) => {
        if (p.updates) { setUpdates(p.updates); return }
        const channel = ['app', 'model', 'config'].includes(p.channel) ? p.channel : 'app'
        setUpdates((current) => ({
          ...current,
          channels: {
            ...(current?.channels || {}),
            [channel]: { ...((current?.channels || {})[channel] || {}), status: 'downloading', progress: p.percent ?? 0 },
          },
        }))
      }),
      on('timer_scheduled', (p) => pushToast(`Timer set: ${p.label || 'Timer'} (${p.minutes} min)`, 'success')),
      on('timer_fired', (p) => { setBanner(`Timer finished: ${p.label || 'Timer'}`); pushToast(`Timer finished: ${p.label || 'Timer'}`, 'success') }),
    ]

    ;(async () => {
      if (gallery) {
        setConversations(demoConversations); setProjects(demoProjects); setLibraryItems(demoLibrary); setLibraryCount(demoLibrary.length)
        setCurrentId('demo-1'); setCurrentConversation(demoConversations[0]); setSelectedProject(demoConversations[0].project_id || '')
        setMessages(demoMessages); setServer(demoServer); setUpdates(demoUpdates); setReady(true); return
      }
      const available = await waitForNative(3000)
      setNative(available); nativeRef.current = available
      if (!available) {
        setConversations(demoConversations); setProjects(demoProjects); setLibraryItems(demoLibrary); setLibraryCount(demoLibrary.length)
        setCurrentId('demo-1'); setCurrentConversation(demoConversations[0]); setSelectedProject(demoConversations[0].project_id || '')
        setMessages(demoMessages); setServer(demoServer); setUpdates(demoUpdates)
        setBanner('Preview mode: launch VISION through the desktop shell to use the real local model, tools, files, and settings.')
        setReady(true); return
      }
      const data = await bridgeApi('get_state')
      if (data) {
        applySettingsData(data)
        setLibraryCount(Number(data.library_count || 0))
        setConversations(data.conversations || [])
        setAttachments(data.attachments || [])
        setCurrentId(String(data.current_conversation_id || '')); currentIdRef.current = String(data.current_conversation_id || '')
        setSelectedProject(String(data.active_project_id || data.config?.active_project_id || ''))
        setEndpoint({ cloud: data.config?.endpoint_mode === 'cloud', kind: data.config?.endpoint_mode || 'local', label: '' })
        if (data.chat_busy) setStreaming(true)
        if (data.current_conversation_id) {
          const conversation = await bridgeApi('get_conversation', data.current_conversation_id)
          if (conversation) { setCurrentConversation(conversation.conversation || null); setMessages(normalizeMessages(conversation.messages || [])); if (conversation.conversation?.project_id !== undefined) setSelectedProject(String(conversation.conversation.project_id || '')) }
        }
        if (data.app?.first_run_completed === false || data.config?.first_run_completed === false) setOverlay('setup')
      }
      await callOptional('frontend_ready', { ok: true })
      setReady(true)
      window.setTimeout(() => inputRef.current?.focus(), 60)
    })().catch((error) => { console.error(error); setBanner(`The interface could not finish loading: ${error.message || error}`); setReady(true) })

    return () => offs.forEach((off) => off?.())
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (overlay === 'settings' && settingsTab === 'tools' && extTab === 'connectors') loadConnectorDirectory()
  }, [overlay, settingsTab, extTab, loadConnectorDirectory])

  useEffect(() => {
    if (!projectsLoadedRef.current || projectFilter === 'all' || projectFilter === 'none') return
    if (!projects.some((project) => String(project.id) === String(projectFilter))) setProjectFilter('all')
  }, [projectFilter, projects])

  useEffect(() => {
    const query = search.trim()
    if (!query) { setSearchRows(null); return undefined }
    let cancelled = false
    const timer = window.setTimeout(async () => {
      if (!nativeRef.current) {
        const lower = query.toLowerCase()
        if (!cancelled) {
          setSearchRows(conversations
            .filter((item) => String(item.title || '').toLowerCase().includes(lower))
            .map((item) => ({ ...item, _search_match: true })))
        }
        return
      }
      const result = await callOptional('list_conversations', null, query)
      const rows = Array.isArray(result) ? result : result?.conversations
      if (!cancelled && Array.isArray(rows)) {
        setSearchRows(rows.map((item) => ({ ...item, _search_match: true })))
      }
    }, 140)
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [conversations, search])

  useEffect(() => {
    if (overlay === 'settings' && settingsTab === 'library') refreshLibrary()
    if (overlay === 'settings' && settingsTab === 'updates') refreshUpdates()
    if (overlay === 'settings' && settingsTab === 'privacy') refreshTelemetry()
    if (overlay === 'settings' && settingsTab === 'tools') { refreshCheckpoints(); if (extTab === 'connectors') loadConnectorDirectory() }
  }, [overlay, refreshCheckpoints, refreshLibrary, refreshTelemetry, refreshUpdates, settingsTab])

  const sidebarConversations = useMemo(() => {
    const usingSearchRows = Array.isArray(searchRows) && Boolean(search.trim())
    const source = usingSearchRows ? searchRows : conversations
    const query = search.trim().toLowerCase()
    return source
      .filter((item) => !item.archived)
      .filter((item) => !query || usingSearchRows || String(item.title || 'New chat').toLowerCase().includes(query))
      .filter((item) => projectFilter === 'all' || (projectFilter === 'none'
        ? !item.project_id
        : String(item.project_id || '') === String(projectFilter)))
      .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || timestampMs(b.updated_at || b.created_at) - timestampMs(a.updated_at || a.created_at))
  }, [conversations, projectFilter, search, searchRows])

  const commands = useMemo(() => [
    { title: 'New chat', hint: 'Start fresh', icon: 'compose', run: newChat },
    { title: 'Regenerate last response', hint: 'Ctrl+Enter', icon: 'refresh', run: regenerate },
    { title: 'Stop generation', hint: 'Esc', icon: 'stop', run: stopGeneration },
    { title: 'Vision Live', hint: 'Push-to-talk voice chat and approved screen frames', icon: 'live', run: openLive },
    { title: 'Attach text files', hint: 'Native picker', icon: 'attach', run: chooseAttachments },
    { title: 'Export conversation', hint: 'Markdown', icon: 'download', run: () => currentId && callApi('export_conversation', currentId) },
    { title: 'Settings', hint: 'General', icon: 'sliders', run: () => openSettings('general') },
    { title: 'Voice', hint: 'Read-aloud voice, style, speed, and pitch', icon: 'speaker', run: () => openSettings('voice') },
    { title: 'Model', hint: 'Downloads, backup connection, and graphics', icon: 'server', run: () => openSettings('model') },
    { title: 'Tools', hint: 'Skills and connected tools', icon: 'puzzle', run: () => openSettings('tools') },
    { title: 'Library', hint: 'Uploaded files', icon: 'library', run: () => openSettings('library') },
    { title: 'Updates', hint: 'App, model, and configuration updates', icon: 'download', run: () => openSettings('updates') },
    { title: 'Item Library', hint: 'Files ready to attach', icon: 'folder', run: openLibrary },
    { title: 'Server console', hint: 'Logs and lifecycle', icon: 'terminal', run: openServer },
    { title: 'Activity & feedback', hint: 'Audit queue', icon: 'pulse', run: openActivity },
    { title: 'Toggle theme', hint: 'Dark / light', icon: theme === 'dark' ? 'sun' : 'moon', run: toggleTheme },
    { title: 'Toggle sidebar', hint: 'Focus mode', icon: 'panel', run: toggleSidebar },
    { title: 'Show sources', hint: 'Citations', icon: 'book', run: () => setInspectorOpen(true) },
    ...conversations.map((row) => ({ title: row.title || 'New chat', hint: 'Open chat', icon: 'message', run: () => selectConversation(row.id) })),
  ], [callApi, chooseAttachments, conversations, currentId, newChat, openActivity, openLibrary, openLive, openServer, openSettings, regenerate, selectConversation, stopGeneration, theme, toggleSidebar, toggleTheme])

  const filteredCommands = commands.filter((item) => !paletteQuery.trim() || `${item.title} ${item.hint}`.toLowerCase().includes(paletteQuery.trim().toLowerCase())).slice(0, 14)

  useEffect(() => {
    const handler = async (event) => {
      const mod = event.ctrlKey || event.metaKey
      if (mod && event.key.toLowerCase() === 'k') { event.preventDefault(); setPaletteQuery(''); setOverlay((v) => v === 'palette' ? '' : 'palette'); return }
      if (mod && event.key.toLowerCase() === 'b') { event.preventDefault(); toggleSidebar(); return }
      if (mod && event.key.toLowerCase() === 'l') { event.preventDefault(); setLibraryOpen((value) => !value); return }
      if (mod && event.key === 'Enter') { event.preventDefault(); regenerate(); return }
      if (event.key !== 'Escape') return
      if (confirmQueue.length) { resolveConfirm(false); return }
      if (promptState) { closePrompt(null); return }
      if (speakingKeyRef.current) { voiceEngine.stop(); return }
      if (overlay) { setOverlay(''); return }
      if (libraryOpen) { setLibraryOpen(false); return }
      if (inspectorOpen) { setInspectorOpen(false); return }
      if (streamingRef.current) stopGeneration()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  })

  const onTranscriptScroll = () => {
    const node = transcriptRef.current
    if (!node) return
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight
    stickRef.current = distance < 140
    setShowJump(!stickRef.current && node.scrollHeight > node.clientHeight + 30)
  }

  const openSource = async (source) => {
    if (source.url) await callOptional('open_url', { ok: false }, source.url)
    else if (source.path) await callOptional('open_source_path', { ok: false }, source.path)
  }
  const showSources = useCallback((list, id = '') => {
    if (Array.isArray(list) && list.length) setSources(list)
    setHighlightSource(id); setInspectorOpen(true)
    if (id) window.setTimeout(() => document.querySelector(`[data-source-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' }), 80)
  }, [])
  const messageHandlers = useMemo(() => ({
    onCopy: copyText,
    onListen: (text, key = '') => {
      // Clicking the speaking message again stops; clicking another one
      // switches to it. voice.js handles markup cleanup, sentence
      // chunking (long answers no longer cut off), and the soothing
      // voice/style from Settings -> Voice.
      if (speakingKeyRef.current && speakingKeyRef.current === key) { voiceEngine.stop(); return }
      voiceEngine.speak(text, ttsPrefsRef.current, key).then((status) => {
        if (status === 'unavailable') pushToast('Read aloud is not available in this window.', 'error')
        else if (status === 'no-voices') pushToast('No speech voices are installed on this PC yet. Add one in Windows Settings → Time & language → Speech.', 'error')
        else if (status === 'empty') pushToast('There is no readable text in this answer.', 'error')
      }).catch(() => pushToast('Read aloud is not available in this window.', 'error'))
    },
    onSource: (id) => showSources(null, id),
    onOpenUrl: (url) => { if (!nativeRef.current) return false; callApi('open_url', url); return true },
    onRegenerate: regenerate,
    onFeedback: async () => { const result = await callApi('record_feedback', currentIdRef.current, 'Thumbs down from React UI'); if (result?.ok) pushToast('Saved to the local feedback queue.', 'success') },
    onCorrect: async () => { const correction = await askPrompt({ title: 'Correct this answer', label: 'What should VISION have said or done?', confirmLabel: 'Save correction', rows: 6 }); if (correction?.trim()) sendText(`/fix ${correction.trim()}`) },
    onSources: (list) => showSources(list),
  }), [askPrompt, callApi, copyText, pushToast, regenerate, sendText, showSources])

  const saveSettings = async () => {
    if (!form) return
    const payload = { ...form, max_extension_tools: Number(form.max_extension_tools || 0), local_port: Number(form.local_port || 8080), context_size: Number(form.context_size || 16384), gpu_layers: Number(form.gpu_layers || 0) }
    setTheme(payload.theme)
    // Voice + motion apply immediately in every mode (including preview,
    // where localStorage is the store); the backend persists them below.
    setTtsPrefs(normalizeTts(payload))
    setReduceMotion(Boolean(payload.reduce_motion))
    if (!nativeRef.current) { setOverlay(''); pushToast('Settings saved locally in preview mode.', 'success'); return }
    const result = await callApi('save_settings', payload)
    if (!result) return
    applySettingsData(result)
    setSelectedProject(payload.active_project_id || '')
    setOverlay(''); pushToast('Settings saved.', 'success')
    if (result.restart_needed) setBanner('Model/server settings changed. Restart the local server from the Server console to apply them.')
  }

  const pickInto = async (kind, field) => {
    const result = await callApi('pick_file', kind)
    if (result?.path) setForm((f) => ({ ...f, [field]: result.path }))
  }
  const finishSetup = async () => { await callApi('finish_setup', { gpu_layers: Number(setupGpu || 99) }); setConfig((c) => ({ ...c, first_run_completed: true, gpu_layers: Number(setupGpu || 99) })); setOverlay('') }

  const latestAssistantKey = useMemo(
    () => [...messages].reverse().find((message) => message.role === 'assistant' && !message.streaming)?.key || '',
    [messages],
  )
  const currentTitle = currentConversation?.title || conversations.find((c) => String(c.id) === String(currentId))?.title || 'New chat'
  const empty = messages.length === 0
  const serverHealthy = server.healthy === true
  const serverTone = serverHealthy ? 'healthy' : server.running || /starting|unhealthy|not checked/i.test(server.state || '') ? 'warn' : 'error'
  const activeConfirm = confirmQueue[0]
  useEffect(() => { setElicitValues({}) }, [activeConfirm?.id])
  const updateChannels = updates?.channels || {}
  const updatesBusy = Boolean(task && ['check_updates', 'download_update', 'apply_update', 'rollback_update'].includes(String(task.name || '')))
  const updateChangelog = updateChannels.app?.notes || ''
  const liveCaps = normalizeLiveCapabilities(liveCapabilities, native)

  return (
    <div className={`app${(hardware?.effective_profile || config?.performance_profile) === 'low_end' ? ' perf-low' : ''}${reduceMotion ? ' motion-reduced' : ''}`}>
      <header className="titlebar pywebview-drag-region">
        <div className="title-left">
          {effectiveCollapsed ? (
            <button type="button" className="title-logo-toggle" title="Expand sidebar" aria-label="Expand sidebar" aria-expanded="false" onClick={toggleSidebar}>
              <LogoMark size={28} />
            </button>
          ) : <LogoMark size={28} />}
          <span className="t-name">VISION</span><span className="t-chat">{currentTitle}</span>
        </div>
        <div className="title-actions">
          <button type="button" className="live-title-btn" onClick={openLive} title="Open Vision Live voice chat"><Icon name="live" size={15} /><span>Live</span></button>
          <button type="button" className="server-pill" onClick={openServer} title="Open server console"><span className={`dot ${serverTone}`} />{gallery ? 'Gallery' : server.state || 'Server not checked'}</button>
          <div className="win-controls">
            <button type="button" className="win-btn" aria-label="Minimize" onClick={() => callOptional('window_minimize', { ok: false })}><Icon name="minus" size={15} /></button>
            <button type="button" className="win-btn" aria-label="Maximize or restore" onClick={() => callOptional('window_maximize', { ok: false })}><Icon name="expand" size={14} /></button>
            <button type="button" className="win-btn close" aria-label="Close" onClick={() => callOptional('window_close', { ok: false })}><Icon name="x" size={15} /></button>
          </div>
        </div>
      </header>

      <div className={`workspace ${effectiveCollapsed ? 'collapsed' : ''}`}>
        <Sidebar
          collapsed={effectiveCollapsed}
          conversations={sidebarConversations}
          projects={projects}
          currentId={currentId}
          search={search}
          onSearchChange={setSearch}
          projectFilter={projectFilter}
          onProjectFilterChange={handleProjectFilterChange}
          collapsedProjects={collapsedProjects}
          onToggleProjectGroup={toggleProjectGroup}
          onToggleCollapsed={toggleSidebar}
          onNewChat={newChat}
          onCreateProject={createProject}
          onSelectConversation={selectConversation}
          onRenameConversation={renameConversationById}
          onTogglePinned={toggleConversationPinned}
          onAssignProject={assignConversationToProject}
          onExportConversation={exportConversationById}
          onDeleteConversation={deleteConversationById}
          onOpenLibrary={openLibrary}
          onOpenServer={openServer}
          onOpenActivity={openActivity}
          onOpenSettings={openSettings}
          onToggleTheme={toggleTheme}
          libraryCount={libraryCount}
          theme={theme}
        />

        <main className="main">
          {banner ? <div className="banner"><Icon name="info" size={16} /><span>{banner}</span><button type="button" className="icon-btn small" aria-label="Dismiss" onClick={() => setBanner('')}><Icon name="x" size={14} /></button></div> : null}
          <section className="transcript" ref={transcriptRef} onScroll={onTranscriptScroll} aria-live="polite">
            <div className={`reading${messages.length > 24 ? ' many' : ''}`}>
              {!ready ? (
                <div className="hero loading-state" aria-busy="true">
                  <LogoMark size={58} />
                  <div className="hero-greet">Loading</div>
                  <h1 className="hero-title">VISION</h1>
                  <p className="hero-sub">Connecting to the local desktop services…</p>
                  <span className="spinner" aria-hidden="true" />
                </div>
              ) : empty ? (
                <div className="hero">
                  <LogoMark size={58} />
                  <div className="hero-greet">{greeting()}</div>
                  <h1 className="hero-title">{updates.ui_strings?.hero_title || 'What can VISION do for you?'}</h1>
                  <p className="hero-sub">{updates.ui_strings?.hero_subtitle || 'A private local assistant for answers, files, live data and desktop actions — running on your machine first.'}</p>
                  <Composer hero agentMode={agentMode} onToggleAgent={toggleAgentMode} onOpenLive={openLive} value={draft} setValue={setDraft} onSubmit={handleComposerSubmit} streaming={streaming} attachments={attachments} removeAttachment={removeAttachment} chooseAttachments={chooseAttachments} endpoint={endpoint} inputRef={inputRef} placeholder={updates.ui_strings?.composer_placeholder || ''} />
                  <div className="suggestions">
                    {[
                      ['cloud', 'Weather in Coimbatore', 'Live local forecast with sources', 'What is the weather in Coimbatore?'],
                      ['cpu', 'System status', 'CPU, RAM, disk and GPU at a glance', 'Show my CPU, RAM, disk and GPU status.'],
                      ['chart', 'Draw a chart', 'Squares from 1 to 4 as bars', 'Draw a bar chart of 1, 4, 9, 16.'],
                      ['appWindow', 'Open an app', 'Launch Notepad on this PC', 'Open Notepad.'],
                    ].map(([icon, title, desc, prompt]) => (
                      <button key={title} type="button" className="suggestion" onClick={() => sendText(prompt)}>
                        <span className="sug-icon"><Icon name={icon} size={17} /></span><span><b>{title}</b><span>{desc}</span></span>
                      </button>
                    ))}
                  </div>
                  <div className="hero-credit">VISION — built by SPIRIT ORG</div>
                </div>
              ) : messages.map((message) => <Message key={message.key} message={message} handlers={messageHandlers} latest={message.key === latestAssistantKey} speaking={speakingKey === message.key} />)}
            </div>
          </section>
          {showJump && !empty ? <button type="button" className="jump" onClick={() => { stickRef.current = true; scrollBottom(true) }}><Icon name="arrowDown" size={14} />Jump to latest</button> : null}
          {!empty ? (
            <section className="composer-dock">
              <Composer agentMode={agentMode} onToggleAgent={toggleAgentMode} onOpenLive={openLive} value={draft} setValue={setDraft} onSubmit={handleComposerSubmit} streaming={streaming} attachments={attachments} removeAttachment={removeAttachment} chooseAttachments={chooseAttachments} endpoint={endpoint} inputRef={inputRef} placeholder={updates.ui_strings?.composer_placeholder || ''} />
            </section>
          ) : null}
        </main>
      </div>

      <aside className={`inspector ${inspectorOpen ? 'open' : ''}`} aria-label="Sources">
        <div className="inspector-head"><h2>Sources</h2><button type="button" className="icon-btn" aria-label="Close sources" onClick={() => setInspectorOpen(false)}><Icon name="x" size={16} /></button></div>
        {sources.length === 0 ? <p className="empty-note">Sources used in the current answer will appear here.</p> : sources.map((source) => {
          const id = source.source_id || source.id || 'S'
          return (
            <article className={`source-card ${highlightSource === id ? 'highlight' : ''}`} key={id} data-source-id={id}>
              <div className="source-top"><span className="source-id">[{id}]</span><button type="button" className="icon-btn small" title="Open source" onClick={() => openSource(source)}><Icon name="external" size={14} /></button></div>
              <h3>{sourceDisplayTitle(source)}</h3>
              {sourceDisplayMeta(source) ? <div className="source-meta">{sourceDisplayMeta(source)}</div> : null}
              {source.snippet ? <p>{source.snippet}</p> : null}
            </article>
          )
        })}
      </aside>

      <LivePanel
        open={liveOpen}
        onClose={() => setLiveOpen(false)}
        onOpenRequest={() => setLiveOpen(true)}
        native={native}
        capabilities={liveCapabilities}
        onRefreshCapabilities={refreshLiveCapabilities}
        callApi={callApi}
        pushToast={pushToast}
        messages={messages}
        streaming={streaming}
        speakingKey={speakingKey}
        ttsPrefs={ttsPrefs}
        onSendLive={sendLiveText}
        onLiveTurnStarted={(key, muted) => { liveSpeechRef.current = { key: String(key || ''), muted: Boolean(muted) } }}
        onStopGeneration={stopGeneration}
        onOpenVoiceSettings={() => openSettings('voice')}
        task={task}
      />

      {libraryOpen ? (
        <Suspense fallback={<div className="library-panel open library-loading"><span className="spinner" />Loading library…</div>}>
          <LibraryPanel
            open={libraryOpen}
            items={libraryItems}
            attachments={attachments}
            loading={libraryLoading}
            onClose={closeLibrary}
            onUpload={importLibraryFiles}
            onAttach={attachLibraryItem}
            onRename={renameLibraryItem}
            onDelete={deleteLibraryItem}
            onRefresh={refreshLibrary}
          />
        </Suspense>
      ) : null}

      {overlay === 'settings' ? (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="sheet wide" role="dialog" aria-modal="true" aria-label="Settings">
            <div className="sheet-head"><h2>Settings</h2><button type="button" className="icon-btn" aria-label="Close settings" onClick={() => setOverlay('')}><Icon name="x" size={17} /></button></div>
            <div className="settings-layout">
              <nav className="settings-nav" role="tablist" aria-label="Settings sections">
                <div className="settings-nav-heading"><span>Sections</span><small>Choose a category</small></div>
                {[
                  ['general', 'spark', 'General', 'Appearance & startup'],
                  ['voice', 'speaker', 'Voice', 'Read aloud & Live'],
                  ['model', 'server', 'Model', 'Local & backup AI'],
                  ['tools', 'puzzle', 'Tools', 'Skills & connections'],
                  ['library', 'library', 'Library', 'Files & checkpoints'],
                  ['privacy', 'shield', 'Privacy', 'Data & feedback'],
                  ['updates', 'download', 'Updates', 'App, model & config'],
                  ['about', 'info', 'About', 'Version & credits'],
                ].map(([id, icon, label, hint]) => (
                  <button key={id} type="button" role="tab" aria-selected={settingsTab === id} className={`settings-tab ${settingsTab === id ? 'active' : ''}`} aria-current={settingsTab === id ? 'page' : undefined} onClick={() => setSettingsTab(id)}>
                    <Icon name={icon} size={16} />
                    <span className="settings-tab-copy"><span>{label}</span><small>{hint}</small></span>
                    <Icon name="chevR" size={13} className="settings-tab-chevron" />
                  </button>
                ))}
              </nav>
              <div className="settings-content">
                {!form ? <p className="empty-note">Loading settings…</p> : settingsTab === 'general' ? (
                  <><h3>General</h3><p className="section-copy">The everyday options, in one quiet place.</p><div className="simple-stack">
                    <div className="field"><label>Appearance</label><select className="select" value={form.theme} onChange={(e) => { setForm({ ...form, theme: e.target.value }); setTheme(e.target.value) }}><option value="dark">Dark</option><option value="light">Light</option></select></div>
                    <div className="field"><label>Project</label><select className="select" value={form.active_project_id} onChange={(e) => setForm({ ...form, active_project_id: e.target.value })}><option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.title || p.id}</option>)}</select><small>New chats open in this project unless you choose another one.</small></div>
                    <label className="check-row"><input type="checkbox" checked={form.auto_start_local_server} onChange={(e) => setForm({ ...form, auto_start_local_server: e.target.checked })} />Start the local model when VISION opens</label>
                    <label className="check-row"><input type="checkbox" checked={Boolean(form.reduce_motion)} onChange={(e) => setForm({ ...form, reduce_motion: e.target.checked })} />Reduce animations — calmer, and faster on small PCs</label>
                  </div>
                  <TechnicalDetails label="Advanced"><div className="field"><label>Maximum connected tools in one request</label><input className="input" type="number" min="0" max="128" value={form.max_extension_tools} onChange={(e) => setForm({ ...form, max_extension_tools: e.target.value })} /></div></TechnicalDetails></>
                ) : settingsTab === 'voice' ? (
                  <><h3>Voice</h3><p className="section-copy">Choose how VISION sounds when you tap <strong>Listen</strong> on an answer. Voices live on this PC and work offline — Auto picks the calmest, most natural one for the style you choose.</p>
                  <StatusRows rows={[['Read aloud', speechAvailable() ? 'Ready' : 'Not available in this window'], ['Voices on this PC', ttsVoices.length ? String(ttsVoices.length) : 'None found yet'], ['Current pick', pickVoice(ttsVoices, normalizeTts(form))?.name || '—']]} />
                  <div className="simple-stack" style={{ marginTop: 18 }}>
                    <div className="field"><label>Voice</label><select id="tts-voice-select" className="select" value={form.tts_voice_uri || ''} disabled={!speechAvailable()} onChange={(e) => { const picked = ttsVoices.find((v) => v.voiceURI === e.target.value); setForm({ ...form, tts_voice_uri: e.target.value, tts_voice_name: picked?.name || '' }) }}>
                      <option value="">{`Auto — soothing pick${pickVoice(ttsVoices, { ...normalizeTts(form), tts_voice_uri: '', tts_voice_name: '' }) ? ` (${voiceOptionLabel(pickVoice(ttsVoices, { ...normalizeTts(form), tts_voice_uri: '', tts_voice_name: '' }))})` : ''}`}</option>
                      {form.tts_voice_uri && !ttsVoices.some((v) => v.voiceURI === form.tts_voice_uri) ? <option value={form.tts_voice_uri}>{`${form.tts_voice_name || 'Saved voice'} (not on this PC anymore)`}</option> : null}
                      {rankVoices(ttsVoices, styleGender(form.tts_style)).map((voice, i) => <option key={`${voice.voiceURI}-${voice.lang}-${i}`} value={voice.voiceURI}>{voiceOptionLabel(voice)}</option>)}
                    </select><small>{ttsVoices.length ? 'Sorted with the most soothing matches first. “Natural” voices sound the calmest; “Online” voices need internet.' : 'No voices reported yet — try Refresh, or add voices in Windows Settings.'}</small></div>
                    <div className="field"><label>Style</label><select className="select" value={form.tts_style || 'soothing'} disabled={!speechAvailable()} onChange={(e) => { const style = e.target.value; const preset = VOICE_STYLES[style]; setForm({ ...form, tts_style: style, ...(preset && preset.rate != null ? { tts_rate: preset.rate, tts_pitch: preset.pitch, tts_volume: preset.volume } : {}) }) }}>
                      {Object.entries(VOICE_STYLES).map(([id, preset]) => <option key={id} value={id}>{preset.label}</option>)}
                    </select><small>Soothing slows down a warm voice slightly and softens its pitch. Moving any slider switches to Custom.</small></div>
                    <div className="field"><label>Speed</label><div className="range-row"><input type="range" aria-label="Speaking speed" min="0.5" max="1.5" step="0.01" value={form.tts_rate} disabled={!speechAvailable()} onChange={(e) => setForm({ ...form, tts_rate: Number(e.target.value), tts_style: 'custom' })} /><span className="range-value">{`${Number(form.tts_rate || 1).toFixed(2)}×`}</span></div></div>
                    <div className="field"><label>Pitch</label><div className="range-row"><input type="range" aria-label="Voice pitch" min="0.5" max="1.5" step="0.01" value={form.tts_pitch} disabled={!speechAvailable()} onChange={(e) => setForm({ ...form, tts_pitch: Number(e.target.value), tts_style: 'custom' })} /><span className="range-value">{Number(form.tts_pitch || 1).toFixed(2)}</span></div></div>
                    <div className="field"><label>Volume</label><div className="range-row"><input type="range" aria-label="Voice volume" min="0" max="1" step="0.05" value={form.tts_volume} disabled={!speechAvailable()} onChange={(e) => setForm({ ...form, tts_volume: Number(e.target.value), tts_style: 'custom' })} /><span className="range-value">{`${Math.round(Number(form.tts_volume ?? 1) * 100)}%`}</span></div></div>
                    <label className="check-row"><input type="checkbox" checked={Boolean(form.tts_auto_read)} disabled={!speechAvailable()} onChange={(e) => setForm({ ...form, tts_auto_read: e.target.checked })} />Read each finished answer aloud automatically</label>
                  </div>
                  <h4 style={{ marginTop: 22 }}>Vision Live</h4>
                  <p className="section-copy">Talk instead of typing with strict push-to-talk. Speech-to-text runs locally after a one-time download; microphone audio is not saved or uploaded. Screen questions use one previewed, approved frame at a time and need a future vision-capable VISION model.</p>
                  <StatusRows rows={[
                    ['Voice chat', liveCaps.speech_to_text.available ? 'Ready offline' : liveCaps.speech_to_text.reason],
                    ['Listening model', liveCaps.speech_to_text.model_downloaded ? liveCaps.speech_to_text.model_label : 'Not downloaded'],
                    ['Voice + screen', liveCaps.screen_vision.available ? `Ready (${liveCaps.screen_vision.mmproj_name})` : liveCaps.screen_vision.reason],
                    ['Screen privacy', 'One local frame, preview + approval, never saved or sent to a backup connection'],
                  ]} />
                  <div className="simple-stack" style={{ marginTop: 18 }}>
                    <div className="field"><label>Speech-to-text model</label><select className="select" value={form.stt_model_size || 'base'} onChange={(e) => setForm({ ...form, stt_model_size: e.target.value })}>
                      {liveCaps.speech_to_text.models.map((model) => <option key={model.size} value={model.size}>{model.label}</option>)}
                    </select><small>Base is the best starting point. Tiny is faster on older CPUs; Small is slower but more accurate.</small></div>
                    <div className="field"><label>Spoken language</label><select className="select" value={form.stt_language || 'auto'} onChange={(e) => setForm({ ...form, stt_language: e.target.value })}>
                      {STT_LANGUAGE_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </select><small>Automatic detects the language from each clip. Language changes apply when you Save.</small></div>
                  </div>
                  <div className="action-row">
                    <button type="button" className="btn primary" disabled={!native || task?.name === 'download_stt'} onClick={() => callApi('download_speech_to_text', { model_size: form.stt_model_size || 'base' })}><Icon name="download" size={15} /> Download local voice model</button>
                    <button type="button" className="btn" onClick={() => { setOverlay(''); openLive() }}><Icon name="live" size={15} /> Open Vision Live</button>
                    <button type="button" className="btn ghost" onClick={refreshLiveCapabilities}>Refresh status</button>
                  </div>
                  {task?.name === 'download_stt' ? <><p className="empty-note" style={{ marginTop: 12 }}>{task.message || 'Downloading…'}{task.percent != null ? ` ${task.percent}%` : ''}{task.total ? ` (${formatBytes(task.downloaded)} / ${formatBytes(task.total)})` : ''}</p>{task.percent != null ? <div className="progress-line"><i style={{ transform: `scaleX(${Math.max(0, Math.min(100, Number(task.percent) || 0)) / 100})` }} /></div> : null}</> : null}
                  <div className="action-row">
                    <button type="button" id="tts-test" className="btn primary" disabled={!speechAvailable()} onClick={() => voiceEngine.speak(SAMPLE_TEXT, normalizeTts(form), '__test__').then((status) => { if (status === 'unavailable') pushToast('Read aloud is not available in this window.', 'error'); else if (status === 'no-voices') pushToast('No speech voices are installed on this PC yet.', 'error') }).catch(() => pushToast('Read aloud is not available in this window.', 'error'))}>Hear a sample</button>
                    {speakingKey ? <button type="button" className="btn" onClick={() => voiceEngine.stop()}>Stop</button> : null}
                    <button type="button" className="btn ghost" onClick={() => loadVoices(2000).then((voices) => { setTtsVoices(voices); pushToast(voices.length ? `Found ${voices.length} voice(s) on this PC.` : 'No speech voices found on this PC yet.', voices.length ? 'success' : 'error') })}>Refresh voices</button>
                  </div>
                  {speechAvailable() ? <p className="empty-note" style={{ marginTop: 12 }}>Want more, or better, voices? Open Windows Settings → Time &amp; language → Speech → Add voices, then Refresh here. Sample changes play immediately; everything else applies when you Save.</p> : <p className="empty-note" style={{ marginTop: 12 }}>This window does not provide speech synthesis, so read-aloud is unavailable here. In the installed VISION app it uses the voices from Windows.</p>}</>
                ) : settingsTab === 'model' ? (
                  <><h3>Model</h3><p className="section-copy">VISION runs on this computer first. Changes here apply the next time the local model starts.</p>
                  <StatusRows rows={serverRows(server)} />
                  <StatusRows rows={modelUpdateRows(updates)} />
                  <div className="simple-stack" style={{ marginTop: 18 }}>
                    <div className="field"><label>Active model</label><select className="select" value={form.model_path} onChange={(e) => setForm({ ...form, model_path: e.target.value })}>
                      {availableModels.map((model) => <option key={model.path} value={model.path}>{model.name} · {formatBytes(model.size)}</option>)}
                      {form.model_path && !availableModels.some((model) => model.path === form.model_path) ? <option value={form.model_path}>{String(form.model_path).split(/[\\/]/).pop()} · custom location</option> : null}
                    </select><small>{availableModels.length ? 'Switch between the GGUF models in your models folder. Applies the next time the model starts. For a file elsewhere, use Browse under Advanced.' : 'Downloaded models will appear here so you can switch between them.'}</small></div>
                    <div className="field"><label>Performance</label><select className="select" value={form.performance_profile || 'auto'} onChange={(e) => setForm({ ...form, performance_profile: e.target.value })}>
                      <option value="auto">Automatic (recommended)</option>
                      <option value="balanced">Balanced</option>
                      <option value="performance">Performance — powerful PC</option>
                      <option value="low_end">Low-end CPU — Intel i3 / no graphics card</option>
                    </select><small>{PERF_PROFILE_COPY[form.performance_profile || 'auto']}{hardware?.logical_cores ? ` Detected ${hardware.logical_cores} CPU threads · ${hardware.total_ram_gb} GB memory${hardware.recommended_profile === 'low_end' ? ' — Low-end CPU mode is recommended for this PC.' : '.'}` : ''} Applies next time the model starts.</small></div>
                    <div className="field"><label>Graphics processing</label><div className="range-row"><input type="range" min="0" max="120" value={form.gpu_layers} onChange={(e) => setForm({ ...form, gpu_layers: e.target.value })} /><span className="range-value">{form.gpu_layers}</span></div><small>Use the highest setting that fits your graphics memory. Lower it if the model cannot start.</small></div>
                  </div>
                  <div className="action-row">
                    <button type="button" className="btn" onClick={() => callApi('download_model')}>Download or repair model</button>
                    <button type="button" className="btn" onClick={() => callApi('download_llama')}>Install runtime</button>
                    <button type="button" className="btn ghost" onClick={() => callApi('check_model_update')}>Check for updates</button>
                  </div>
                  <TechnicalDetails label="Advanced model settings" rows={serverTechnicalRows(server)}>
                    <div className="form-grid technical-grid">
                      <div className="field full"><label>Model file</label><div className="field-row"><input className="input" value={form.model_path} onChange={(e) => setForm({ ...form, model_path: e.target.value })} /><button type="button" className="btn" onClick={() => pickInto('model', 'model_path')}>Browse</button></div></div>
                      <div className="field full"><label>Vision projector (mmproj, future models)</label><div className="field-row"><input className="input" placeholder="Leave blank for today's text-only VISION model" value={form.mmproj_path || ''} onChange={(e) => setForm({ ...form, mmproj_path: e.target.value })} /><button type="button" className="btn" onClick={() => pickInto('model', 'mmproj_path')}>Browse</button></div><small>Only set this with the exact mmproj file published for a future vision-capable VISION GGUF. A mismatch can stop the local model from starting; leave it blank today.</small></div>
                      <div className="field full"><label>Runtime file</label><div className="field-row"><input className="input" value={form.llama_server_path} onChange={(e) => setForm({ ...form, llama_server_path: e.target.value })} /><button type="button" className="btn" onClick={() => pickInto('server', 'llama_server_path')}>Browse</button></div></div>
                      <div className="field"><label>Local port</label><input className="input" type="number" min="1" max="65535" value={form.local_port} onChange={(e) => setForm({ ...form, local_port: e.target.value })} /></div>
                      <div className="field"><label>Context size</label><input className="input" type="number" min="1024" max="262144" step="1024" value={form.context_size} onChange={(e) => setForm({ ...form, context_size: e.target.value })} /></div>
                      <div className="field full"><label>Local model name (optional)</label><input className="input" placeholder="Use the installed local model" value={form.local_model_override} onChange={(e) => setForm({ ...form, local_model_override: e.target.value })} /></div>
                    </div>
                  </TechnicalDetails>
                  {task ? <><p className="empty-note" style={{ marginTop: 12 }}>{task.message || 'Working…'}{task.percent != null ? ` ${task.percent}%` : ''}{task.total ? ` (${formatBytes(task.downloaded)} / ${formatBytes(task.total)})` : ''}</p>{task.percent != null ? <div className="progress-line"><i style={{ transform: `scaleX(${Math.max(0, Math.min(100, Number(task.percent) || 0)) / 100})` }} /></div> : null}</> : null}
                  <h4>Backup connection</h4><p className="section-copy">Choose where answers come from. Keys are masked here and stored securely by Windows; VISION never shows a saved key again.</p>
                  <StatusRows rows={[['Connection', form.remote_base_url || (form.remote_base_url_configured && !form.clear_remote_base_url) ? 'Backup connection configured' : 'Local only'], ['Saved key', credentials.remote_api_key_stored ? 'Saved securely' : 'Not saved']]} />
                  <div className="simple-stack" style={{ marginTop: 18 }}>
                    <div className="field"><label>Answer source</label><select className="select" value={form.endpoint_mode} onChange={(e) => setForm({ ...form, endpoint_mode: e.target.value })}><option value="auto">Automatic (local first)</option><option value="local">This computer only</option><option value="cloud">Backup connection only</option></select><small>Automatic uses the backup connection only if the local model is unavailable.</small></div>
                    <div className="field"><label>Backup connection key</label><input className="input" type="password" autoComplete="new-password" placeholder={credentials.remote_api_key_stored ? '••••••••••••' : 'Enter a new key'} value={form.remote_api_key} onChange={(e) => setForm({ ...form, remote_api_key: e.target.value, clear_remote_api_key: false })} /><small>{credentials.remote_api_key_stored ? 'A key is already saved. Leave this blank to keep it.' : 'Optional. It will be saved in Windows Credential Manager.'}</small></div>
                    {credentials.remote_api_key_stored ? <label className="check-row"><input type="checkbox" checked={form.clear_remote_api_key} onChange={(e) => setForm({ ...form, clear_remote_api_key: e.target.checked, remote_api_key: '' })} />Remove the saved key when I save</label> : null}
                  </div>
                  <TechnicalDetails label="Advanced connection settings">
                    <div className="form-grid technical-grid">
                      <div className="field full"><label>Backup connection address</label><input className="input" type="password" autoComplete="new-password" placeholder={form.remote_base_url_configured ? 'Saved address hidden — enter a new one to replace it' : 'Paste the service address'} value={form.remote_base_url} onChange={(e) => setForm({ ...form, remote_base_url: e.target.value, clear_remote_base_url: false })} /><small>For privacy, a saved address is never shown again. Leave this blank to keep it.</small></div>
                      {form.remote_base_url_configured ? <label className="check-row"><input type="checkbox" checked={form.clear_remote_base_url} onChange={(e) => setForm({ ...form, clear_remote_base_url: e.target.checked, remote_base_url: '' })} />Remove the saved connection address</label> : null}
                      <div className="field full"><label>Backup model name (optional)</label><input className="input" autoComplete="off" placeholder="Use the service default" value={form.remote_model_override} onChange={(e) => setForm({ ...form, remote_model_override: e.target.value })} /></div>
                    </div>
                  </TechnicalDetails></>
                ) : settingsTab === 'privacy' ? (
                  <><h3>Privacy</h3><p className="section-copy">Your chats and feedback stay on this device unless you choose to send feedback.</p>
                  <StatusRows rows={[['Storage', paths.installation_mode === 'portable' ? 'Portable folder' : paths.installation_mode === 'custom' ? 'Custom location' : 'This Windows account'], ['Feedback upload', form.telemetry_opt_in ? 'Allowed on the background schedule and when VISION closes' : 'Off'], ['Pending telemetry', `${telemetry.pending_count ?? telemetry.status?.pending_events ?? 0} event(s)`], ['Last upload', telemetryStatusLabel(telemetry.status || {})], ['Download access key', credentials.hf_token_stored ? 'Saved securely' : 'Not saved']]} />
                  <div className="simple-stack" style={{ marginTop: 18 }}>
                    <label className="check-row"><input type="checkbox" checked={form.telemetry_opt_in} onChange={(e) => setForm({ ...form, telemetry_opt_in: e.target.checked })} />Allow queued feedback to upload in the background (off by default)</label>
                    <div className="action-row">
                      <button type="button" className="btn small" onClick={openTelemetryPreview}>Preview what will be sent</button>
                      <button type="button" className="btn small ghost" onClick={refreshTelemetry}>Refresh status</button>
                      <button type="button" className="btn small danger" onClick={clearLocalTelemetry}>{telemetryClearArmed ? 'Click again to clear' : 'Clear local telemetry'}</button>
                    </div>
                    {telemetry.status?.message ? <small>{telemetry.status.message}</small> : null}
                    <div className="field"><label>Download access key</label><input className="input" type="password" autoComplete="new-password" placeholder={credentials.hf_token_stored ? '••••••••••••' : 'Enter a new key'} value={form.hf_token} onChange={(e) => setForm({ ...form, hf_token: e.target.value, clear_hf_token: false })} /><small>{credentials.hf_token_stored ? 'A key is already saved. Leave this blank to keep it.' : 'Only needed for protected downloads or sending feedback.'}</small></div>
                    {credentials.hf_token_stored ? <label className="check-row"><input type="checkbox" checked={form.clear_hf_token} onChange={(e) => setForm({ ...form, clear_hf_token: e.target.checked, hf_token: '' })} />Remove the saved key when I save</label> : null}
                  </div>
                  <TechnicalDetails label="Storage locations" rows={[['Installation', paths.installation_root || 'Development run'], ['App data', paths.app_data], ['Workspace', paths.workspace]]} /></>
                ) : settingsTab === 'tools' ? (
                  <><h3>Extensions</h3><p className="section-copy">Three different systems: <strong>MCP servers</strong> add new tools, <strong>skills</strong> add know-how, and <strong>connectors</strong> are one-click MCP services with sign-in handled. Extension tools ask once per session before they run.</p>
                  <div className="update-preferences">
                    <label className="check-row"><input type="checkbox" checked={extensions.extensions_enabled !== false} onChange={async (e) => { const r = await callApi('set_extensions_enabled', e.target.checked); if (r?.extensions) setExtensions(r.extensions); pushToast(e.target.checked ? 'Extensions on.' : 'Extensions off — built-in tools only.') }} />Extensions enabled</label>
                    <span className="budget-line" title="Tools sent to the model on the last turn, and their estimated size">{extensions.routing?.tool_count != null ? `${extensions.routing.tool_count}/${extensions.routing.max_tools || 40} tools · ~${((extensions.routing.estimated_tokens || 0) / 1000).toFixed(1)}k tokens` : 'Tool budget appears after your next message'}</span>
                  </div>
                  {extensions.routing?.included_extension_tools?.length || extensions.routing?.dropped?.length ? (
                    <TechnicalDetails label="Tool budget (debug)" rows={[['Included extension tools', (extensions.routing.included_extension_tools || []).join(', ') || 'None'], ['Dropped', (extensions.routing.dropped || []).map((d) => `${d.name} (${d.reason})`).join(', ') || 'None']]}>
                      <p className="empty-note" style={{ margin: 0 }}>Built-in tools are always sent. Extension tools are ranked against your message and added until the budget (40 tools, ~4k tokens of definitions) fills.</p>
                    </TechnicalDetails>
                  ) : null}
                  <div className="ext-tabs" role="tablist" aria-label="Extension systems">
                    {[['mcp', 'MCP Servers'], ['skills', 'Skills'], ['connectors', 'Connectors']].map(([id, label]) => (
                      <button key={id} type="button" role="tab" aria-selected={extTab === id} className={extTab === id ? 'active' : ''} onClick={() => setExtTab(id)}>{label}</button>
                    ))}
                  </div>
                  {extTab === 'skills' ? (<>
                  <h4>Skills</h4>
                  <p className="section-copy">Skills are folders with a <code>SKILL.md</code> playbook. Only names and one-liners are indexed; a skill's full instructions load when they're relevant (or when you type <code>/skill-name</code>).</p>
                  {skills.length === 0 ? <p className="empty-note">No skills are installed yet.</p> : skills.map((skill) => (
                    <article className="extension-card" key={skill.name}><div><h5>{skill.name}</h5><p>{skill.description || 'No description'}</p><div className="extension-meta">{(skill.script_count ?? skill.scripts?.length ?? 0) ? 'Includes scripts that require approval' : 'Instructions only'}</div>
                      <div className="action-row"><button type="button" className="btn small ghost" onClick={() => previewSkill(skill.name)}>Preview</button><button type="button" className="btn small ghost" onClick={() => callApi('open_skill_folder', skill.name)}>Open folder</button><button type="button" className="btn small ghost" onClick={async () => { const r = await callApi('export_skill', skill.name); if (r?.ok) pushToast(`Exported ${r.path || 'skill'}.`, 'success'); else if (!r?.cancelled) pushToast(r?.message || 'Export failed.', 'error') }}>Export</button>
                      <button type="button" className="btn small danger" onClick={async () => { if (extDeleteArmed !== `skill:${skill.name}`) { setExtDeleteArmed(`skill:${skill.name}`); return } setExtDeleteArmed(''); const r = await callApi('delete_skill', skill.name); if (r?.ok && r.skills) { setSkills(r.skills); pushToast('Skill deleted.', 'success') } else pushToast(r?.message || 'Could not delete.', 'error') }}>{extDeleteArmed === `skill:${skill.name}` ? 'Click again to delete' : 'Delete'}</button></div></div>
                      <label className="switch-row"><input type="checkbox" checked={form.enabled_skills.includes(skill.name)} onChange={(e) => setForm({ ...form, enabled_skills: e.target.checked ? [...form.enabled_skills, skill.name] : form.enabled_skills.filter((x) => x !== skill.name) })} />Enabled</label></article>
                  ))}
                  <div className="action-row"><button type="button" className="btn small" onClick={() => setSkillEditor({ name: '', description: '', instructions: '' })}>New skill…</button><button type="button" className="btn small ghost" onClick={() => callApi('import_skill', false)}>Import skill…</button><button type="button" className="btn small ghost" onClick={() => callApi('import_skill', true)}>Import folder…</button></div>
                  {skillPreview ? (
                    <div className="skill-preview">
                      <div className="cap-head"><h5 style={{ margin: 0 }}>{skillPreview.name}</h5><span className="grow" /><button type="button" className="btn small ghost" onClick={() => setSkillPreview(null)}>Close preview</button></div>
                      <p className="section-copy">{skillPreview.description}</p>
                      <pre className="json-block" style={{ maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{skillPreview.body}</pre>
                      {skillPreview.files?.length ? <div className="extension-meta">Files: {skillPreview.files.join(', ')}</div> : null}
                    </div>
                  ) : null}
                  </>) : null}
                  {extTab !== 'skills' ? (<>
                  {extTab === 'connectors' ? (<>
                    <h4>Directory</h4>
                    <p className="section-copy">One-click connectors from the config channel. Installing stores the key in Windows Credential Manager and connects through MCP — nothing else to configure.</p>
                    {connectorDirectory.length === 0 ? <p className="empty-note">No directory connectors are published for this channel yet. You can still add a custom connector below.</p> : connectorDirectory.map((entry) => (
                      <article className="extension-card" key={entry.id}><div><h5>{entry.name}</h5><p>{entry.description || 'No description'}</p><div className="extension-meta">{entry.auth_type === 'none' ? 'No sign-in' : entry.auth_type === 'oauth' ? 'Browser sign-in (OAuth)' : 'API key'}{entry.scopes?.length ? ` · Scopes: ${entry.scopes.join(', ')}` : ''}{entry.auto_approve ? ' · Preset tools auto-approved' : ''}</div></div>
                        {entry.installed ? <span className="update-pill up_to_date">Connected</span> : <button type="button" className="btn small primary" onClick={() => installDirectoryConnector(entry)}>Install</button>}
                      </article>
                    ))}
                  </>) : null}
                  <h4>{extTab === 'connectors' ? 'Your connectors' : 'MCP servers'}</h4>
                  <p className="section-copy">{extTab === 'connectors' ? 'Remote MCP services with their sign-in stored securely. Disconnect removes both the credential and the service.' : 'Tool servers VISION connects to over the Model Context Protocol. New servers stay off their tools until you Check and trust the exact tool list. Drop-in files in the extensions servers folder are imported automatically.'}</p>
                  {[[extTab === 'connectors' ? 'connector' : 'mcp', extTab === 'connectors' ? (extensions.connectors || []) : (extensions.servers || [])]].flatMap(([kind, items]) => items.map((item) => {
                    const activeCount = Number(item.active_tool_count || 0)
                    const trusted = Boolean(item.trusted ?? item.toolset_sha256)
                    const technicalTarget = item.destination_label || (item.url ? displayHost(item.url) : item.command ? displayFileName(item.command) : 'Configured privately')
                    const promptArgs = (prompt, kind2, id2) => (async () => {
                      const args = {}
                      for (const arg of prompt.arguments || []) {
                        const value = await askPrompt({ title: `Prompt: ${prompt.name}`, label: `${arg.name}${arg.required ? ' (required)' : ''}${arg.description ? ` — ${arg.description}` : ''}`, confirmLabel: 'Use', rows: 2 })
                        if (value == null) return
                        if (String(value).trim()) args[arg.name] = String(value)
                      }
                      pushToast('Getting prompt…'); await callApi('get_mcp_prompt', kind2, id2, prompt.name, args)
                    })()
                    return (
                      <article className="extension-card" key={`${kind}-${item.id}`}><div><h5>{item.display_name || 'Connected tool'}</h5>
                        <div className="extension-meta"><span className={`dot ${item.status === 'running' ? 'healthy' : item.status === 'error' ? 'error' : ''}`} title={item.status || 'stopped'} /> {item.status === 'running' ? 'Running' : item.status === 'error' ? 'Error' : 'Stopped'} · {kind === 'connector' ? 'Connector' : 'MCP server'} · {item.transport} · {trusted ? 'Trusted' : 'Needs review'} · {activeCount} tool{activeCount === 1 ? '' : 's'} live{item.unavailable ? ' · Unavailable (timed out)' : ''}{item.imported ? ' · From servers folder' : ''}{item.stale ? ' · Changed — refresh' : ''}{item.progress?.total ? ` · Progress ${item.progress.progress}/${item.progress.total}` : ''}</div>
                        {item.last_error ? <p className="empty-note">{item.last_error}</p> : null}
                        {(item.discovered_tools || []).length ? <div className="extension-meta">Tools: {item.discovered_tools.map((t) => `${t.name}${t.read_only_hint ? ' (read-only)' : ''}${t.destructive_hint ? ' (destructive)' : ''}`).join(', ')}</div> : null}
                        {(item.resources || []).filter((r) => !r.template).length ? (<div className="ext-sublist"><div className="detail-label">Resources</div>{item.resources.filter((r) => !r.template).map((r) => (<div className="ext-subrow" key={r.uri}><span title={r.uri}>{r.name}</span><button type="button" className="btn small ghost" onClick={async () => { pushToast('Reading resource…'); await callApi('attach_mcp_resource', kind, item.id, r.uri) }}>Attach</button></div>))}</div>) : null}
                        {(item.prompts || []).length ? (<div className="ext-sublist"><div className="detail-label">Prompts</div>{item.prompts.map((pr) => (<div className="ext-subrow" key={pr.name}><span title={pr.description}>{pr.name}</span><button type="button" className="btn small ghost" onClick={() => promptArgs(pr, kind, item.id)}>Use</button></div>))}</div>) : null}
                        <TechnicalDetails label="Connection details" rows={[['Type', kind === 'connector' ? 'Connector' : 'MCP server'], ['Destination', technicalTarget], ['Available tools', activeCount], ['Sampling', item.allow_sampling ? 'Allowed (asks each time)' : 'Off'], ['Questions from server', item.allow_elicitation === false ? 'Off' : 'On']]}>
                          {(item.logs || []).length ? <pre className="json-block">{item.logs.join('\n')}</pre> : null}
                        </TechnicalDetails>
                        <div className="action-row">
                          <button type="button" className="btn small" onClick={() => openExtEditor(kind, item)}>Edit</button>
                          {item.imported ? null : <button type="button" className="btn small danger" onClick={() => deleteExtension(kind, item)}>{extDeleteArmed === `${kind}:${item.id}` ? (kind === 'connector' ? 'Click again to disconnect' : 'Click again to delete') : (kind === 'connector' ? 'Disconnect' : 'Delete')}</button>}
                        </div>
                        </div>
                        <div className="ext-card-actions">
                          <label className="switch-row"><input type="checkbox" checked={Boolean(item.enabled)} onChange={(e) => toggleExtensionEnabled(kind, item, e.target.checked)} />Enabled</label>
                          <button type="button" className="btn small" onClick={async () => { pushToast(`Checking ${item.display_name || 'extension'}…`); await callApi('discover_extension', kind, item.id) }}>{trusted ? 'Check again' : 'Review & trust'}</button>
                        </div></article>
                    )
                  }))}
                  {(extTab === 'connectors' ? (extensions.connectors?.length || 0) : (extensions.servers?.length || 0)) === 0 ? <p className="empty-note">{extTab === 'connectors' ? 'No connectors installed yet.' : 'No MCP servers yet. Add one below.'}</p> : null}
                  <div className="action-row">{extTab === 'connectors' ? <button type="button" className="btn small" onClick={() => openExtEditor('connector')}>Add custom connector…</button> : <button type="button" className="btn small primary" onClick={() => openExtEditor('mcp')}>Add MCP server…</button>}</div>
                  {extTab === 'mcp' ? (<>
                  <h4>Code checkpoints</h4>
                  <p className="section-copy">Before VISION changes a code file it saves a checkpoint. Restore brings that file back to the checkpoint's contents (or removes it if it did not exist yet).</p>
                  {codeCheckpoints.length === 0 ? <p className="empty-note">No checkpoints saved yet.</p> : codeCheckpoints.slice(0, 20).map((checkpoint) => (
                    <article className="extension-card" key={checkpoint.id}><div><h5>{checkpoint.file_name || 'File'}</h5><p>{checkpoint.original_path}</p><div className="extension-meta">{checkpoint.created_at ? formatDate(checkpoint.created_at) : ''}{checkpoint.existed ? '' : ' · file was new'}</div></div>
                      <button type="button" className="btn small" onClick={() => { if (restoreCheckpointArmed !== checkpoint.id) { setRestoreCheckpointArmed(checkpoint.id); return } setRestoreCheckpointArmed(''); callApi('restore_checkpoint', checkpoint.id) }}>{restoreCheckpointArmed === checkpoint.id ? 'Click again to restore' : 'Restore'}</button></article>
                  ))}
                  <div className="action-row"><button type="button" className="btn small ghost" onClick={refreshCheckpoints}>Refresh checkpoints</button></div>
                  </>) : null}
                  </>) : null}
                  {extensions.messages?.length ? <TechnicalDetails label="Tool notices"><p className="empty-note" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{extensions.messages.join('\n')}</p></TechnicalDetails> : null}
                  <div className="action-row"><button type="button" className="btn small" onClick={() => callApi('refresh_extensions')}>Refresh</button></div></>
                ) : settingsTab === 'library' ? (
                  <Suspense fallback={<p className="empty-note">Loading library…</p>}>
                    <LibraryPanel
                      embedded
                      items={libraryItems}
                      attachments={attachments}
                      loading={libraryLoading}
                      onUpload={importLibraryFiles}
                      onAttach={attachLibraryItem}
                      onRename={renameLibraryItem}
                      onDelete={deleteLibraryItem}
                      onRefresh={refreshLibrary}
                    />
                  </Suspense>
                ) : settingsTab === 'updates' ? (
                  <><h3>Updates</h3><p className="section-copy">VISION checks three independent channels. App downloads are verified first and are never applied until you choose to restart.</p>
                  {!updates.enabled ? <p className="update-notice">Update downloads are currently turned off in this build. The status below is local and no update files will be downloaded.</p> : null}
                  {updates.offline ? <p className="update-notice">VISION is offline for update checks. Last known status is shown below.</p> : null}
                  {updates.enabled && !updates.launcher_managed ? <p className="update-notice">This copy can check for app updates, but applying an app update after restart needs the launcher-managed setup layout. Run the latest VISION setup once to enable hands-off app staging and rollback.</p> : null}
                  <div className="update-preferences">
                    <label className="check-row"><input type="checkbox" checked={updates.auto_check !== false} onChange={(e) => changeUpdatePreferences({ auto_check: e.target.checked })} />Automatically check for updates when VISION opens</label>
                    <button type="button" className="btn small" disabled={updatesBusy} onClick={() => checkUpdateChannel('all')}>Check all now</button>
                  </div>
                  <div className="update-list">
                    <UpdateChannelRow channel="app" label="App" description="The VISION desktop application. Updates download in the background and apply only after restart." icon="download" row={updateChannels.app} busy={updatesBusy} onCheck={checkUpdateChannel} onDownload={downloadUpdateChannel} onRestart={restartToApplyUpdate} onRollback={rollbackUpdate} />
                    <UpdateChannelRow channel="model" label="Model" description="The local VISION model, updated weekly. A downloaded model activates on the next launch, never in the middle of a conversation. Several days without a new build is normal." icon="server" row={updateChannels.model} busy={updatesBusy} onCheck={checkUpdateChannel} onDownload={downloadUpdateChannel} onRestart={restartToApplyUpdate} onRollback={rollbackUpdate} />
                    <UpdateChannelRow channel="config" label="Config" description="Prompts, tool definitions, and interface text. Validated changes can apply immediately without restarting." icon="sliders" row={updateChannels.config} busy={updatesBusy} onCheck={checkUpdateChannel} onDownload={downloadUpdateChannel} onRestart={restartToApplyUpdate} onRollback={rollbackUpdate} />
                  </div>
                  <h4>Changelog</h4>
                  <div className="update-notes">{updateChangelog || 'No release notes are available for the installed version yet.'}</div>
                  <TechnicalDetails label="Advanced update options" rows={[['Channel', updates.channel === 'dev' ? 'Development' : 'Stable'], ['Launcher-managed install', updates.launcher_managed ? 'Yes' : 'No'], ['Last checked', updates.last_checked ? formatDate(updates.last_checked) : 'Not checked']]}>
                    <div className="field"><label>Update channel</label><select className="select" value={updates.channel || 'stable'} onChange={(e) => changeUpdatePreferences({ channel: e.target.value })}><option value="stable">Stable</option><option value="dev">Development</option></select><small>Development builds may be unfinished. Most people should stay on Stable.</small></div>
                  </TechnicalDetails></>
                ) : (
                  <><h3>About</h3><p className="section-copy">A private, local-first assistant for this computer.</p>
                  <StatusRows rows={[['Product', 'VISION Desktop'], ['Version', appInfo.version || '—'], ['Credits', 'VISION — built by SPIRIT ORG'], ['Mode', 'Works locally first; optional connections are configured by you.']]} /></>
                )}
              </div>
            </div>
            <div className="sheet-foot"><button type="button" className="btn ghost" onClick={() => { setTheme(config.theme || 'dark'); setOverlay('') }}>Cancel</button><button type="button" className="btn primary" onClick={saveSettings}>Save settings</button></div>
          </section>
        </div>
      ) : null}

      {telemetryPreview ? (
        <div className="overlay telemetry-preview-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setTelemetryPreview(null) }}>
          <section className="sheet wide" role="dialog" aria-modal="true" aria-label="Telemetry upload preview">
            <div className="sheet-head"><h2>Preview what will be sent</h2><button type="button" className="icon-btn" aria-label="Close telemetry preview" onClick={() => setTelemetryPreview(null)}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body simple-page">
              <p className="section-copy">This is the exact local JSONL payload that would be append-merged after the dataset's existing rows. Secrets and absolute local paths are redacted before events are written. Nothing is sent unless feedback upload is allowed or you explicitly send it.</p>
              <StatusRows rows={[['Pending events', telemetryPreview.event_count ?? telemetry.pending_count ?? 0], ['Last upload', telemetryStatusLabel(telemetry.status || {})]]} />
              <h4>needs.jsonl</h4>
              <pre className="server-log telemetry-json">{telemetryPreview.needs_jsonl || 'No pending telemetry events.'}</pre>
              <h4>report.json</h4>
              <pre className="server-log telemetry-json">{JSON.stringify(telemetryPreview.report || {}, null, 2)}</pre>
            </div>
          </section>
        </div>
      ) : null}

      {overlay === 'server' ? (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="sheet" role="dialog" aria-modal="true" aria-label="Server console">
            <div className="sheet-head"><h2>Server console</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => setOverlay('')}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body simple-page">
              <p className="section-copy">Start or stop the model running on this computer.</p>
              <StatusRows rows={serverRows(server)} />
              <div className="action-row">
                <button type="button" className="btn primary" onClick={() => callApi('start_server')}>Start</button>
                <button type="button" className="btn" onClick={() => callApi('restart_server')}>Restart</button>
                <button type="button" className="btn danger" onClick={() => callApi('stop_server')}>Stop</button>
                <button type="button" className="btn ghost" onClick={openServer}>Refresh</button>
              </div>
              <TechnicalDetails label="Advanced details" rows={serverTechnicalRows(server)} />
              <TechnicalDetails label="Diagnostics">
                <pre className="server-log">{serverLogs.join('\n') || 'No diagnostics yet.'}</pre>
              </TechnicalDetails>
            </div>
          </section>
        </div>
      ) : null}

      {overlay === 'activity' ? (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="sheet wide" role="dialog" aria-modal="true" aria-label="Activity and feedback">
            <div className="sheet-head"><h2>Activity & feedback</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => setOverlay('')}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body simple-page">
              <p className="section-copy">A private record of tool activity and feedback waiting on this device.</p>
              <StatusRows rows={[['Feedback waiting', activity?.feedback_count ?? 0], ['Shown below', activity?.feedback_rows?.length ?? 0]]} />
              <div className="action-row">
                <button type="button" className="btn" onClick={async () => { const result = await callApi('export_feedback'); if (result?.ok) pushToast('Feedback exported.', 'success') }}>Export feedback</button>
                <button type="button" className="btn" onClick={() => callApi('upload_feedback')}>Send feedback</button>
                <button type="button" className="btn danger" onClick={async () => { if (!clearArmed) { setClearArmed(true); return } await callApi('clear_feedback'); setClearArmed(false); openActivity() }}>{clearArmed ? 'Click again to clear' : 'Clear feedback'}</button>
              </div>
              <TechnicalDetails label="Recent activity"><pre className="server-log" style={{ height: 220 }}>{activity?.audit_text || 'No activity yet.'}</pre></TechnicalDetails>
              <TechnicalDetails label="Feedback details"><pre className="server-log" style={{ height: 220 }}>{(activity?.feedback_rows || []).map((row) => JSON.stringify(row)).join('\n') || 'Feedback queue is empty.'}</pre></TechnicalDetails>
            </div>
          </section>
        </div>
      ) : null}

      {overlay === 'setup' ? (
        <div className="overlay">
          <section className="sheet" role="dialog" aria-modal="true" aria-label="Welcome" style={{ width: 'min(660px,100%)' }}>
            <div className="sheet-head"><h2>Welcome to VISION</h2></div>
            <div className="sheet-body">
              <p className="section-copy">Download the local model and supported llama-server, then run a health check. You can skip and finish later in Settings.</p>
              <StatusRows rows={[['Model', server.model_exists ? 'Installed' : 'Not installed'], ['Runtime', server.binary ? (server.build_supported ? 'Ready' : 'Update required') : 'Not installed'], ['Status', server.state || 'Not checked']]} />
              <div className="field" style={{ marginTop: 16 }}><label>GPU layers</label><div className="range-row"><input type="range" min="0" max="120" value={setupGpu} onChange={(e) => setSetupGpu(e.target.value)} /><span className="range-value">{setupGpu}</span></div><small>99 offloads as much as possible. Lower it if the GPU runs out of VRAM.</small></div>
              {task ? <p className="empty-note" style={{ marginTop: 12 }}>{task.message || 'Working…'}{task.percent != null ? ` ${task.percent}%` : ''}</p> : null}
              <div className="action-row">
                <button type="button" className="btn" onClick={() => callApi('download_model')}>1. Download model</button>
                <button type="button" className="btn" onClick={() => callApi('download_llama')}>2. Install runtime</button>
                <button type="button" className="btn" onClick={() => callApi('start_server')}>3. Start / health check</button>
              </div>
            </div>
            <div className="sheet-foot"><button type="button" className="btn ghost" onClick={finishSetup}>Skip for now</button><button type="button" className="btn primary" onClick={finishSetup}>Finish</button></div>
          </section>
        </div>
      ) : null}

      {overlay === 'palette' ? (
        <div className="overlay palette-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
            <input className="palette-input" autoFocus value={paletteQuery} onChange={(e) => setPaletteQuery(e.target.value)} placeholder="Type a command or chat title…" onKeyDown={(e) => { if (e.key === 'Enter' && filteredCommands[0]) { setOverlay(''); filteredCommands[0].run() } }} />
            <div className="palette-list">
              {filteredCommands.length === 0 ? <p className="empty-note" style={{ padding: 10 }}>No matching command.</p> : filteredCommands.map((item, i) => (
                <button key={`${item.title}-${i}`} type="button" className={`palette-item ${i === 0 ? 'selected' : ''}`} onClick={async () => { setOverlay(''); await item.run() }}><Icon name={item.icon} size={16} /><span>{item.title}</span><span className="palette-hint">{item.hint}</span></button>
              ))}
            </div>
          </section>
        </div>
      ) : null}

      {promptState ? (
        <div className="overlay">
          <section className="sheet" role="dialog" aria-modal="true" style={{ width: 'min(520px,100%)' }}>
            <div className="sheet-head"><h2>{promptState.title}</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => closePrompt(null)}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body"><div className="field"><label>{promptState.label}</label><textarea className="textarea" autoFocus rows={promptState.rows} value={promptValue} onChange={(e) => setPromptValue(e.target.value)} /></div></div>
            <div className="sheet-foot"><button type="button" className="btn ghost" onClick={() => closePrompt(null)}>Cancel</button><button type="button" className="btn primary" onClick={() => closePrompt(promptValue)}>{promptState.confirmLabel}</button></div>
          </section>
        </div>
      ) : null}

      {activeConfirm ? (
        <div className="overlay">
          <section className="sheet confirmation-card" role="alertdialog" aria-modal="true">
            <div className="sheet-head"><h2>{activeConfirm.title}</h2></div>
            <div className="sheet-body"><p className="section-copy" style={{ whiteSpace: 'pre-wrap' }}>{activeConfirm.message}</p>
              {activeConfirm.details?.elicitation_fields ? (
                <>
                  <p className="empty-note">Only share what you're comfortable sending to this connected tool. Leave fields blank to skip them.</p>
                  {activeConfirm.details.elicitation_fields.map((field) => (
                    <div className="field" key={field.name}><label>{field.title || field.name}{field.required ? ' *' : ''}</label>
                      {field.enum?.length ? (
                        <select className="select" value={elicitValues[field.name] ?? ''} onChange={(e) => setElicitValues((v) => ({ ...v, [field.name]: e.target.value }))}><option value="">Choose…</option>{field.enum.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select>
                      ) : field.type === 'boolean' ? (
                        <label className="check-row"><input type="checkbox" checked={Boolean(elicitValues[field.name])} onChange={(e) => setElicitValues((v) => ({ ...v, [field.name]: e.target.checked }))} />Yes</label>
                      ) : (
                        <input className="input" type={field.type === 'number' || field.type === 'integer' ? 'number' : 'text'} value={elicitValues[field.name] ?? ''} onChange={(e) => setElicitValues((v) => ({ ...v, [field.name]: e.target.value }))} />
                      )}
                      {field.description ? <small>{field.description}</small> : null}</div>
                  ))}
                </>
              ) : (<><div className="detail-label">Details</div><pre className="json-block">{typeof activeConfirm.details === 'string' ? activeConfirm.details : JSON.stringify(activeConfirm.details || {}, null, 2)}</pre></>)}
            </div>
            <div className="sheet-foot"><button type="button" className="btn danger" onClick={() => resolveConfirm(false)}>{activeConfirm.details?.elicitation_fields ? 'Decline' : (activeConfirm.denyLabel || 'Deny')}</button><button type="button" className="btn primary" autoFocus onClick={() => resolveConfirm(true)}>{activeConfirm.details?.elicitation_fields ? 'Send answers' : (activeConfirm.approveLabel || 'Allow')}</button></div>
          </section>
        </div>
      ) : null}

      {extEditor ? (
        <div className="overlay">
          <section className="sheet" role="dialog" aria-modal="true" style={{ width: 'min(640px,100%)' }}>
            <div className="sheet-head"><h2>{extEditor.isNew ? (extEditor.kind === 'connector' ? 'Add connector' : 'Add MCP server') : `Edit ${extEditor.values.display_name || 'extension'}`}</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => setExtEditor(null)}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body">
              {(() => { const v = extEditor.values; const set = (patch) => setExtEditor((e) => ({ ...e, values: { ...e.values, ...patch } })); const remote = v.transport !== 'stdio'; return (<>
              <div className="field"><label>Display name</label><input className="input" value={v.display_name || ''} onChange={(e) => set({ display_name: e.target.value })} placeholder="e.g. Filesystem tools" /></div>
              {extEditor.isNew ? <div className="field"><label>ID (lowercase letters, numbers, hyphens)</label><input className="input" value={v.id || ''} onChange={(e) => set({ id: e.target.value })} placeholder="e.g. my-tools" /></div> : null}
              <div className="field"><label>Connection type</label><select className="select" value={v.transport || 'stdio'} onChange={(e) => set({ transport: e.target.value })}>
                {extEditor.kind === 'mcp' ? <option value="stdio">Command on this computer (stdio)</option> : null}
                <option value="streamable_http">Remote server (Streamable HTTP)</option>
                <option value="sse">Remote server (SSE, older)</option>
              </select></div>
              {remote ? <div className="field"><label>Server URL</label><input className="input" value={v.url || ''} onChange={(e) => set({ url: e.target.value })} placeholder="https://example.com/mcp" /></div> : (<>
                <div className="field"><label>Command</label><input className="input" value={v.command || ''} onChange={(e) => set({ command: e.target.value })} placeholder="e.g. npx or python" /><small>This program will run on your computer. Only add commands you trust.</small></div>
                <div className="field"><label>Arguments</label><input className="input" value={v.args || ''} onChange={(e) => set({ args: e.target.value })} placeholder="-y @modelcontextprotocol/server-filesystem C:\Users\you\Documents" /></div>
                <div className="field"><label>Working directory (optional)</label><input className="input" value={v.cwd || ''} onChange={(e) => set({ cwd: e.target.value })} placeholder="Blank = VISION workspace" /></div>
              </>)}
              {extEditor.kind === 'connector' ? (<>
                <div className="field"><label>Sign-in</label><select className="select" value={v.auth_type || 'none'} onChange={(e) => set({ auth_type: e.target.value })}><option value="none">None</option><option value="bearer">Bearer token</option><option value="api_key">API key header</option></select><small>Browser sign-in (OAuth) is not built in yet — services that require it can't connect until it is added.</small></div>
                {v.auth_type === 'api_key' ? <div className="field"><label>API key header name</label><input className="input" value={v.auth_header || ''} onChange={(e) => set({ auth_header: e.target.value })} placeholder="X-API-Key" /></div> : null}
              </>) : null}
              {(extEditor.kind === 'connector' ? (v.auth_type && v.auth_type !== 'none') : !remote) ? (
                <div className="field"><label>{extEditor.kind === 'connector' ? 'Secret (stored in Windows Credential Manager)' : 'Secret value (optional)'}</label><input className="input" type="password" autoComplete="new-password" value={v.secret || ''} onChange={(e) => set({ secret: e.target.value })} placeholder={v.header_secret_names?.length || v.env_secret_names?.length ? 'Leave blank to keep the stored secret' : ''} />
                {extEditor.kind === 'mcp' && !remote ? <><label>Environment variable name for the secret</label><input className="input" value={v.secret_env_name || 'API_KEY'} onChange={(e) => set({ secret_env_name: e.target.value })} /></> : null}
                {(v.header_secret_names?.length || v.env_secret_names?.length) ? <label className="check-row"><input type="checkbox" checked={Boolean(v.clear_secret)} onChange={(e) => set({ clear_secret: e.target.checked })} />Remove the stored secret</label> : null}</div>
              ) : null}
              <label className="check-row"><input type="checkbox" checked={Boolean(v.enabled)} onChange={(e) => set({ enabled: e.target.checked })} />Enabled</label>
              <label className="check-row"><input type="checkbox" checked={Boolean(v.auto_approve)} onChange={(e) => set({ auto_approve: e.target.checked })} />Auto-approve this server's tools (skip the once-per-session prompt)</label>
              <label className="check-row"><input type="checkbox" checked={v.allow_elicitation !== false} onChange={(e) => set({ allow_elicitation: e.target.checked })} />Allow this server to ask me questions while it works</label>
              <label className="check-row"><input type="checkbox" checked={Boolean(v.allow_sampling)} onChange={(e) => set({ allow_sampling: e.target.checked })} />Allow this server to ask VISION's model to generate text (you approve every request)</label>
              <div className="field"><label>Request timeout (seconds)</label><input className="input" type="number" min="5" max="600" value={v.timeout_seconds ?? 60} onChange={(e) => set({ timeout_seconds: Number(e.target.value) || 60 })} /></div>
              <div className="field"><label>Allowed folders (optional, one per line)</label><textarea className="textarea" rows={2} value={v.allow_paths || ''} onChange={(e) => set({ allow_paths: e.target.value })} placeholder="C:\Users\you\Documents" /><small>If set, this server's tools are blocked from touching paths outside these folders.</small></div>
              {(() => { const names = [...new Set([...(extEditor.discovered || []).map((t) => t.name), ...Object.keys(v.permissions || {})])].filter(Boolean); return names.length ? (<div className="field"><label>Per-tool permissions</label>{names.map((name) => (<div className="ext-subrow" key={name}><span>{name}</span><select className="select" style={{ width: 140 }} value={(v.permissions || {})[name] || 'ask'} onChange={(e) => set({ permissions: { ...(v.permissions || {}), [name]: e.target.value } })}><option value="ask">Ask</option><option value="allow">Allow</option><option value="blocked">Blocked</option></select></div>))}</div>) : null })()}
              </>) })()}
            </div>
            <div className="sheet-foot"><button type="button" className="btn ghost" onClick={() => setExtEditor(null)}>Cancel</button><button type="button" className="btn primary" onClick={saveExtEditor}>Save</button></div>
          </section>
        </div>
      ) : null}

      {skillEditor ? (
        <div className="overlay">
          <section className="sheet" role="dialog" aria-modal="true" style={{ width: 'min(640px,100%)' }}>
            <div className="sheet-head"><h2>New skill</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => setSkillEditor(null)}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body">
              <div className="field"><label>Name (lowercase, hyphens)</label><input className="input" value={skillEditor.name} onChange={(e) => setSkillEditor((v) => ({ ...v, name: e.target.value }))} placeholder="e.g. weekly-report" /></div>
              <div className="field"><label>When should VISION use it?</label><input className="input" value={skillEditor.description} onChange={(e) => setSkillEditor((v) => ({ ...v, description: e.target.value }))} placeholder="Use when the user asks for a weekly status report…" /></div>
              <div className="field"><label>Instructions</label><textarea className="textarea" rows={10} value={skillEditor.instructions} onChange={(e) => setSkillEditor((v) => ({ ...v, instructions: e.target.value }))} placeholder="Step-by-step instructions VISION should follow…" /></div>
            </div>
            <div className="sheet-foot"><button type="button" className="btn ghost" onClick={() => setSkillEditor(null)}>Cancel</button><button type="button" className="btn primary" onClick={async () => { const r = await callApi('create_skill', skillEditor); if (r?.ok) { if (r.skills) setSkills(r.skills); setSkillEditor(null); pushToast('Skill created. Enable it in the list above.', 'success') } else pushToast(r?.message || 'Could not create the skill.', 'error') }}>Create skill</button></div>
          </section>
        </div>
      ) : null}

      <div className="toast-stack" aria-live="polite">{toasts.map((toast) => <div className={`toast ${toast.kind}`} key={toast.id}><Icon name={toast.kind === 'error' ? 'x' : toast.kind === 'success' ? 'check' : 'info'} size={16} /><span>{toast.message}</span></div>)}</div>
    </div>
  )
}
