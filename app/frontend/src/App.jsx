import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api as bridgeApi, callOptional, on, waitForNative } from './bridge.js'
import { Icon, LogoMark, toolCategory, TOOL_ICONS } from './icons.jsx'
import { Markdown, extractThink } from './markdown.jsx'
import { GenWidget, systemStatusPayload } from './widgets.jsx'
import { demoConversations, demoMessages, demoServer } from './demo.js'

const uid = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`

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
function formatDate(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || !n) return ''
  return new Date(n < 1e10 ? n * 1000 : n).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}
function dateGroup(value, pinned) {
  if (pinned) return 'Pinned'
  const n = Number(value) || 0
  const d = new Date(n < 1e10 ? n * 1000 : n)
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  if (day >= start) return 'Today'
  if (day >= start - 86400000) return 'Yesterday'
  return 'Earlier'
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

function ToolCard({ tool }) {
  const [open, setOpen] = useState(false)
  const [, force] = useState(0)
  useEffect(() => {
    if (tool.status !== 'running') return undefined
    const timer = window.setInterval(() => force((v) => v + 1), 300)
    return () => window.clearInterval(timer)
  }, [tool.status])
  const widget = tool.genui || systemStatusPayload(tool)
  const duration = tool.status === 'running' ? formatDuration(Date.now() - (tool.startedAt || Date.now()), 'running') : formatDuration(tool.durationMs, '')
  return (
    <article className={`tool-card ${tool.status}`}>
      <button type="button" className="tool-head" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="tool-icon"><Icon name={TOOL_ICONS[toolCategory(tool.name)] || 'puzzle'} size={16} /></span>
        <span className="tool-main">
          <span className="tool-name-row"><span className="tool-name">{tool.name}</span><span className="tool-dur">{duration}</span></span>
          <span className="tool-args">{oneLine(tool.args || {})}</span>
        </span>
        <span className="tool-status">{tool.status === 'running' ? <span className="spinner" /> : <Icon name={tool.status === 'error' ? 'x' : 'check'} size={16} />}</span>
      </button>
      {widget ? <div className="widget-slot"><GenWidget payload={widget} /></div> : null}
      {open ? (
        <div className="tool-details">
          <p className="tool-summary">{tool.summary || (tool.status === 'running' ? 'Running…' : 'Completed')}</p>
          <div className="detail-label">Arguments</div><pre className="json-block">{pretty(tool.args || {})}</pre>
          {tool.status !== 'running' ? <><div className="detail-label">{tool.status === 'error' ? 'Error / result' : 'Result'}</div><pre className="json-block">{pretty(tool.raw ?? tool.content ?? '')}</pre></> : null}
        </div>
      ) : null}
    </article>
  )
}

function Message({ message, handlers }) {
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
        <Reasoning text={reasoning} streaming={message.streaming} />
        {message.tools?.length ? <div className="tool-stack">{message.tools.map((tool) => <ToolCard key={tool.id || tool.name} tool={tool} />)}</div> : null}
        {content || message.streaming ? <><Markdown text={content} {...handlers} />{message.streaming ? <span className="stream-cursor" /> : null}</> : null}
        {!message.streaming ? (
          <div className="msg-actions">
            <button type="button" className="icon-btn small" title="Copy" onClick={() => handlers.onCopy?.(content)}><Icon name="copy" size={15} /></button>
            <button type="button" className="icon-btn small" title="Regenerate" onClick={handlers.onRegenerate}><Icon name="refresh" size={15} /></button>
            <button type="button" className="icon-btn small" title="Not helpful" onClick={handlers.onFeedback}><Icon name="thumb" size={15} /></button>
            <button type="button" className="icon-btn small" title="Correct this" onClick={handlers.onCorrect}><Icon name="pencil" size={15} /></button>
            {message.sources?.length ? <button type="button" className="btn small ghost sources-link" onClick={() => handlers.onSources?.(message.sources)}>Sources</button> : null}
          </div>
        ) : null}
      </div>
    </article></div>
  )
}

function Composer({ value, setValue, onSubmit, streaming, attachments, removeAttachment, chooseAttachments, endpoint, inputRef, hero = false }) {
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
          value={value} rows={1} placeholder={hero ? 'How can I help you today?' : 'Message VISION…'} aria-label="Message VISION"
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); onSubmit() } }}
        />
        <div className="composer-row">
          <button type="button" className="attach-btn" title="Attach text files" aria-label="Attach text files" disabled={streaming} onClick={chooseAttachments}><Icon name="plus" size={18} /></button>
          <span className="model-chip" title={endpoint.label || endpoint.kind}><span className={`dot ${endpoint.cloud ? 'warn' : 'healthy'}`} />{endpoint.cloud ? 'CLOUD' : 'LOCAL'}</span>
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
}

function StatusRows({ rows }) {
  return <div className="status-card"><dl>{rows.map(([k, v]) => <React.Fragment key={k}><dt>{k}</dt><dd>{v == null || v === '' ? '—' : String(v)}</dd></React.Fragment>)}</dl></div>
}

function serverRows(server = {}, fallbackUrl = '') {
  const build = server.build ? `b${server.build}` : 'Unknown'
  return [
    ['State', server.state || 'Not checked'], ['Process', server.running ? 'Running' : 'Stopped'],
    ['Local URL', server.local_base_url || fallbackUrl || 'http://127.0.0.1:8080/v1'], ['Health failures', server.failures ?? 0],
    ['Model', server.model_exists ? `${formatBytes(server.model_size)} installed` : 'Missing'], ['Model path', server.model_path],
    ['llama-server', server.binary || 'Not found'], ['Build', `${build} (minimum b${server.minimum_build || 11815}, ${server.build_supported ? 'supported' : 'not supported'})`],
    ['Command', Array.isArray(server.command) ? server.command.join(' ') : '—'], ['Exit code', server.exit_code ?? '—'],
  ]
}

export default function App() {
  const gallery = useMemo(() => new URLSearchParams(window.location.search).has('gallery'), [])
  const [native, setNative] = useState(false)
  const [ready, setReady] = useState(false)
  const [theme, setTheme] = useState(() => localStorage.getItem('vision.theme') || 'dark')
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('vision.sidebarCollapsed') === '1')
  const [appInfo, setAppInfo] = useState({})
  const [config, setConfig] = useState({})
  const [credentials, setCredentials] = useState({})
  const [paths, setPaths] = useState({})
  const [projects, setProjects] = useState([])
  const [skills, setSkills] = useState([])
  const [extensions, setExtensions] = useState({ servers: [], connectors: [], dynamic_tools: [], messages: [] })
  const [conversations, setConversations] = useState([])
  const [currentId, setCurrentId] = useState('')
  const [currentConversation, setCurrentConversation] = useState(null)
  const [selectedProject, setSelectedProject] = useState('')
  const [search, setSearch] = useState('')
  const [messages, setMessages] = useState([])
  const [sources, setSources] = useState([])
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [highlightSource, setHighlightSource] = useState('')
  const [attachments, setAttachments] = useState([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [endpoint, setEndpoint] = useState({ cloud: false, kind: 'local', label: '' })
  const [server, setServer] = useState({})
  const [serverLogs, setServerLogs] = useState([])
  const [banner, setBanner] = useState('')
  const [toasts, setToasts] = useState([])
  const [task, setTask] = useState(null)
  const [overlay, setOverlay] = useState('') // settings | server | activity | setup | palette
  const [settingsTab, setSettingsTab] = useState('general')
  const [form, setForm] = useState(null)
  const [activity, setActivity] = useState(null)
  const [clearArmed, setClearArmed] = useState(false)
  const [paletteQuery, setPaletteQuery] = useState('')
  const [confirmQueue, setConfirmQueue] = useState([])
  const [promptState, setPromptState] = useState(null)
  const [promptValue, setPromptValue] = useState('')
  const [showJump, setShowJump] = useState(false)
  const [setupGpu, setSetupGpu] = useState(99)

  const nativeRef = useRef(false)
  const streamingRef = useRef(false)
  const currentIdRef = useRef('')
  const assistantKeyRef = useRef('')
  const stickRef = useRef(true)
  const transcriptRef = useRef(null)
  const inputRef = useRef(null)
  const simTimersRef = useRef([])

  useEffect(() => { nativeRef.current = native }, [native])
  useEffect(() => { streamingRef.current = streaming }, [streaming])
  useEffect(() => { currentIdRef.current = currentId }, [currentId])
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('vision.theme', theme) }, [theme])
  useEffect(() => { localStorage.setItem('vision.sidebarCollapsed', collapsed ? '1' : '0') }, [collapsed])

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

  const scrollBottom = useCallback((force = false) => {
    const node = transcriptRef.current
    if (!node || (!force && !stickRef.current)) return
    const apply = () => { node.scrollTop = node.scrollHeight; setShowJump(false) }
    requestAnimationFrame(apply)
    window.setTimeout(apply, 90)
    window.setTimeout(apply, 260)
  }, [])

  useEffect(() => { if (stickRef.current) scrollBottom() }, [messages, scrollBottom])

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
    if (item.kind === 'native') await callOptional('resolve_confirmation', { ok: false }, item.id, Boolean(approved))
    else item.resolve?.(Boolean(approved))
  }, [confirmQueue])

  const askPrompt = useCallback(({ title, label, value = '', confirmLabel = 'Save', rows = 5 }) => new Promise((resolve) => {
    setPromptValue(value)
    setPromptState({ title, label, confirmLabel, rows, resolve })
  }), [])
  const closePrompt = useCallback((value) => {
    promptState?.resolve?.(value)
    setPromptState(null)
  }, [promptState])

  const applySettingsData = useCallback((data = {}) => {
    if (data.app) setAppInfo(data.app)
    if (data.config) { setConfig((c) => ({ ...c, ...data.config })); if (data.config.theme) setTheme(data.config.theme); if (data.config.gpu_layers != null) setSetupGpu(data.config.gpu_layers) }
    if (data.credentials) setCredentials(data.credentials)
    if (data.paths) setPaths((p) => ({ ...p, ...data.paths }))
    if (data.projects) setProjects(data.projects)
    if (data.skills) setSkills(data.skills)
    if (data.extensions) setExtensions(data.extensions)
    if (data.server) setServer((s) => ({ ...s, ...data.server }))
  }, [])

  const openSettings = useCallback(async (tab = 'general') => {
    setSettingsTab(tab); setOverlay('settings')
    let data = null
    if (nativeRef.current) data = await callApi('get_settings')
    if (data) applySettingsData(data)
    const c = { ...config, ...(data?.config || {}) }
    const skillList = data?.skills || skills
    setForm({
      theme: c.theme || theme, endpoint_mode: c.endpoint_mode || 'auto', active_project_id: c.active_project_id || '',
      max_extension_tools: c.max_extension_tools ?? 32, auto_start_local_server: Boolean(c.auto_start_local_server),
      model_path: c.model_path || data?.paths?.model_file || paths.model_file || '', llama_server_path: c.llama_server_path || '',
      local_port: c.local_port ?? 8080, context_size: c.context_size ?? 16384, gpu_layers: c.gpu_layers ?? 99,
      local_model_override: c.local_model_override || '', remote_base_url: c.remote_base_url || '', remote_model_override: c.remote_model_override || '',
      telemetry_opt_in: Boolean(c.telemetry_opt_in), enabled_skills: skillList.filter((s) => s.enabled).map((s) => s.name),
      remote_api_key: '', clear_remote_api_key: false, hf_token: '', clear_hf_token: false,
    })
  }, [applySettingsData, callApi, config, paths.model_file, skills, theme])

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
    if (nativeRef.current) { const data = await callApi('get_activity'); if (data) setActivity(data) }
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

  const newChat = useCallback(async () => {
    if (streamingRef.current) { pushToast('Stop the current response before starting another chat.', 'error'); return }
    if (!nativeRef.current) {
      const id = `local-${uid()}`
      setConversations((items) => [{ id, title: 'New chat', updated_at: Date.now() / 1000 }, ...items])
      setCurrentId(id); setCurrentConversation({ id, title: 'New chat' }); setMessages([]); setSources([]); return
    }
    const result = await callApi('create_conversation', selectedProject || '')
    if (!result) return
    if (result.conversations) setConversations(result.conversations)
    setCurrentConversation(result.conversation || null); setCurrentId(String(result.conversation?.id || '')); setMessages([]); setSources([])
  }, [callApi, pushToast, selectedProject])

  const stopGeneration = useCallback(async () => {
    simTimersRef.current.forEach((timer) => window.clearTimeout(timer)); simTimersRef.current = []
    if (nativeRef.current) await callApi('stop_generation')
    if (!nativeRef.current) { setStreaming(false); updateAssistant((m) => ({ ...m, streaming: false, incomplete: true, content: m.content || 'Generation stopped.' })) }
  }, [callApi, updateAssistant])

  const simulatePreview = useCallback((text) => {
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
    after(1250 + words.length * 35, () => { setStreaming(false); updateAssistant((m) => ({ ...m, streaming: false })) })
  }, [updateAssistant])

  const sendText = useCallback(async (raw) => {
    const text = String(raw || '').trim()
    if (!text || streamingRef.current) return
    const command = text.toLowerCase()
    if (command === '/settings') { setDraft(''); openSettings(); return }
    if (command === '/server') { setDraft(''); openServer(); return }
    if (command === '/activity') { setDraft(''); openActivity(); return }
    if (command === '/new') { setDraft(''); newChat(); return }
    if (command === '/help') { setDraft(''); setOverlay('palette'); return }

    const assistantKey = uid(); assistantKeyRef.current = assistantKey
    setMessages((items) => [...items,
      { key: uid(), role: 'user', content: text, tools: [] },
      { key: assistantKey, role: 'assistant', content: '', reasoning: '', tools: [], sources: [], streaming: true }])
    setSources([]); setDraft(''); setStreaming(true); stickRef.current = true
    if (!nativeRef.current) { simulatePreview(text); setAttachments([]); return }
    const attachmentIds = attachments.map((item) => item.id)
    const result = await callApi('send_message', { text, conversation_id: currentIdRef.current || '', project_id: selectedProject || '', attachment_ids: attachmentIds })
    if (!result?.accepted) {
      setStreaming(false)
      updateAssistant((m) => ({ ...m, streaming: false, incomplete: true, content: result?.message || 'The message could not be started.' }))
      return
    }
    setAttachments([])
  }, [attachments, callApi, newChat, openActivity, openServer, openSettings, selectedProject, simulatePreview, updateAssistant])

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

  const handleTaskFinished = useCallback(async (payload = {}) => {
    setTask(null)
    if (!payload.ok) { pushToast(payload.error || `${payload.name || 'Task'} failed`, 'error'); return }
    const result = payload.result || {}
    pushToast(result.message || 'Done', 'success')
    if (result.server) setServer((s) => ({ ...s, ...result.server }))
    if (result.config) setConfig((c) => ({ ...c, ...result.config }))
    if (result.skills) setSkills(result.skills)
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
        if (Array.isArray(p.conversations)) setConversations(p.conversations)
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
      on('timer_scheduled', (p) => pushToast(`Timer set: ${p.label || 'Timer'} (${p.minutes} min)`, 'success')),
      on('timer_fired', (p) => { setBanner(`Timer finished: ${p.label || 'Timer'}`); pushToast(`Timer finished: ${p.label || 'Timer'}`, 'success') }),
    ]

    ;(async () => {
      if (gallery) {
        setConversations(demoConversations); setCurrentId('demo-1'); setCurrentConversation(demoConversations[0])
        setMessages(demoMessages); setServer(demoServer); setReady(true); return
      }
      const available = await waitForNative(3000)
      setNative(available); nativeRef.current = available
      if (!available) {
        setConversations(demoConversations); setCurrentId('demo-1'); setCurrentConversation(demoConversations[0])
        setMessages(demoMessages); setServer(demoServer)
        setBanner('Preview mode: launch VISION through the desktop shell to use the real local model, tools, files, and settings.')
        setReady(true); return
      }
      const data = await bridgeApi('get_state')
      if (data) {
        applySettingsData(data)
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

  const sortedConversations = useMemo(() => [...conversations]
    .filter((item) => !item.archived)
    .filter((item) => !search.trim() || String(item.title || 'New chat').toLowerCase().includes(search.trim().toLowerCase()))
    .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || Number(b.updated_at || 0) - Number(a.updated_at || 0)), [conversations, search])

  const commands = useMemo(() => [
    { title: 'New chat', hint: 'Start fresh', icon: 'compose', run: newChat },
    { title: 'Regenerate last response', hint: 'Ctrl+Enter', icon: 'refresh', run: regenerate },
    { title: 'Stop generation', hint: 'Esc', icon: 'stop', run: stopGeneration },
    { title: 'Attach text files', hint: 'Native picker', icon: 'attach', run: chooseAttachments },
    { title: 'Export conversation', hint: 'Markdown', icon: 'download', run: () => currentId && callApi('export_conversation', currentId) },
    { title: 'Settings', hint: 'General', icon: 'sliders', run: () => openSettings('general') },
    { title: 'Model & server', hint: 'Downloads and GPU', icon: 'server', run: () => openSettings('model') },
    { title: 'Extensions', hint: 'Skills and MCP', icon: 'puzzle', run: () => openSettings('extensions') },
    { title: 'Server console', hint: 'Logs and lifecycle', icon: 'terminal', run: openServer },
    { title: 'Activity & feedback', hint: 'Audit queue', icon: 'pulse', run: openActivity },
    { title: 'Toggle theme', hint: 'Dark / light', icon: theme === 'dark' ? 'sun' : 'moon', run: toggleTheme },
    { title: 'Toggle sidebar', hint: 'Focus mode', icon: 'panel', run: () => setCollapsed((v) => !v) },
    { title: 'Show sources', hint: 'Citations', icon: 'book', run: () => setInspectorOpen(true) },
    ...conversations.map((row) => ({ title: row.title || 'New chat', hint: 'Open chat', icon: 'message', run: () => selectConversation(row.id) })),
  ], [callApi, chooseAttachments, conversations, currentId, newChat, openActivity, openServer, openSettings, regenerate, selectConversation, stopGeneration, theme, toggleTheme])

  const filteredCommands = commands.filter((item) => !paletteQuery.trim() || `${item.title} ${item.hint}`.toLowerCase().includes(paletteQuery.trim().toLowerCase())).slice(0, 14)

  useEffect(() => {
    const handler = async (event) => {
      const mod = event.ctrlKey || event.metaKey
      if (mod && event.key.toLowerCase() === 'k') { event.preventDefault(); setPaletteQuery(''); setOverlay((v) => v === 'palette' ? '' : 'palette'); return }
      if (mod && event.key === 'Enter') { event.preventDefault(); regenerate(); return }
      if (event.key !== 'Escape') return
      if (confirmQueue.length) { resolveConfirm(false); return }
      if (promptState) { closePrompt(null); return }
      if (overlay) { setOverlay(''); return }
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
  const showSources = (list, id = '') => {
    if (Array.isArray(list) && list.length) setSources(list)
    setHighlightSource(id); setInspectorOpen(true)
    if (id) window.setTimeout(() => document.querySelector(`[data-source-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' }), 80)
  }
  const messageHandlers = {
    onCopy: copyText,
    onSource: (id) => showSources(null, id),
    onOpenUrl: (url) => { if (!nativeRef.current) return false; callApi('open_url', url); return true },
    onRegenerate: regenerate,
    onFeedback: async () => { const result = await callApi('record_feedback', currentIdRef.current, 'Thumbs down from React UI'); if (result?.ok) pushToast('Saved to the local feedback queue.', 'success') },
    onCorrect: async () => { const correction = await askPrompt({ title: 'Correct this answer', label: 'What should VISION have said or done?', confirmLabel: 'Save correction', rows: 6 }); if (correction?.trim()) sendText(`/fix ${correction.trim()}`) },
    onSources: (list) => showSources(list),
  }

  const saveSettings = async () => {
    if (!form) return
    const payload = { ...form, max_extension_tools: Number(form.max_extension_tools || 0), local_port: Number(form.local_port || 8080), context_size: Number(form.context_size || 16384), gpu_layers: Number(form.gpu_layers || 0) }
    setTheme(payload.theme)
    if (!nativeRef.current) { setOverlay(''); pushToast('Theme saved locally in preview mode.', 'success'); return }
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

  const currentTitle = currentConversation?.title || conversations.find((c) => String(c.id) === String(currentId))?.title || 'New chat'
  const empty = messages.length === 0
  const serverHealthy = server.healthy === true
  const serverTone = serverHealthy ? 'healthy' : server.running || /starting|unhealthy|not checked/i.test(server.state || '') ? 'warn' : 'error'
  const activeConfirm = confirmQueue[0]

  let lastGroup = ''
  return (
    <div className="app">
      <header className="titlebar pywebview-drag-region">
        <div className="title-left"><LogoMark size={28} /><span className="t-name">VISION</span><span className="t-chat">{currentTitle}</span></div>
        <div className="title-actions">
          <button type="button" className="server-pill" onClick={openServer} title="Open server console"><span className={`dot ${serverTone}`} />{gallery ? 'Gallery' : server.state || 'Server not checked'}</button>
          <div className="win-controls">
            <button type="button" className="win-btn" aria-label="Minimize" onClick={() => callOptional('window_minimize', { ok: false })}><Icon name="minus" size={15} /></button>
            <button type="button" className="win-btn" aria-label="Maximize or restore" onClick={() => callOptional('window_maximize', { ok: false })}><Icon name="expand" size={14} /></button>
            <button type="button" className="win-btn close" aria-label="Close" onClick={() => callOptional('window_close', { ok: false })}><Icon name="x" size={15} /></button>
          </div>
        </div>
      </header>

      <div className={`workspace ${collapsed ? 'collapsed' : ''}`}>
        <aside className="sidebar" aria-label="Conversations">
          <div className="brand">
            <LogoMark size={34} />
            <div className="brand-text"><div className="word">VISION</div><div className="by">built by SPIRIT ORG</div></div>
            <button type="button" className="icon-btn" title="Toggle sidebar" onClick={() => setCollapsed((v) => !v)}><Icon name="panel" size={17} /></button>
          </div>
          <button type="button" className="new-chat" onClick={newChat}><Icon name="compose" size={17} /><span>New chat</span></button>
          <div className="search"><Icon name="search" size={15} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search chats" aria-label="Search chats" /></div>
          <div className="side-label">Project</div>
          <select className="project-select" value={selectedProject} aria-label="Active project" onChange={async (e) => { setSelectedProject(e.target.value); const result = await callApi('set_active_project', e.target.value, currentId || ''); if (result?.projects) setProjects(result.projects) }}>
            <option value="">No project</option>
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name || project.title || project.id}</option>)}
          </select>
          <div className="side-label">Chats</div>
          <nav className="chat-list">
            {sortedConversations.length === 0 ? <p className="empty-note" style={{ padding: '0 8px' }}>{search ? 'No chats match your search.' : 'No chats yet.'}</p> : sortedConversations.map((row) => {
              const group = dateGroup(row.updated_at || row.created_at, row.pinned)
              const heading = group !== lastGroup ? group : ''
              lastGroup = group
              return (
                <React.Fragment key={row.id}>
                  {heading ? <div className="chat-group">{heading}</div> : null}
                  <div className={`chat-item ${String(row.id) === String(currentId) ? 'active' : ''}`}>
                    <button type="button" className="chat-main" title={row.title || 'New chat'} onClick={() => selectConversation(row.id)}>
                      <Icon name={row.pinned ? 'pin' : 'message'} size={15} /><span className="chat-title">{row.title || 'New chat'}</span><span className="chat-date">{formatDate(row.updated_at || row.created_at)}</span>
                    </button>
                    <span className="chat-actions">
                      <button type="button" className="icon-btn small" title="Rename" onClick={async () => { const title = await askPrompt({ title: 'Rename chat', label: 'Chat title', value: row.title || '', confirmLabel: 'Rename', rows: 1 }); if (title?.trim()) { const result = await callApi('rename_conversation', row.id, title.trim()); if (result?.conversations) setConversations(result.conversations); if (!nativeRef.current) setConversations((items) => items.map((x) => x.id === row.id ? { ...x, title: title.trim() } : x)) } }}><Icon name="pencil" size={13} /></button>
                      <button type="button" className="icon-btn small" title={row.pinned ? 'Unpin' : 'Pin'} onClick={async () => { const result = await callApi('set_conversation_pinned', row.id, !row.pinned); if (result?.conversations) setConversations(result.conversations); if (!nativeRef.current) setConversations((items) => items.map((x) => x.id === row.id ? { ...x, pinned: !x.pinned } : x)) }}><Icon name="pin" size={13} /></button>
                      <button type="button" className="icon-btn small" title="Delete" onClick={async () => { const ok = await localConfirm('Delete this chat?', 'The conversation and its messages will be permanently removed from local history.', { title: row.title || 'New chat' }, { approveLabel: 'Delete', denyLabel: 'Cancel' }); if (!ok) return; const result = await callApi('delete_conversation', row.id); if (result?.conversations) { setConversations(result.conversations); const next = result.current_conversation_id || result.conversations[0]?.id; if (next) selectConversation(next, true) } else if (!nativeRef.current) setConversations((items) => items.filter((x) => x.id !== row.id)) }}><Icon name="trash" size={13} /></button>
                    </span>
                  </div>
                </React.Fragment>
              )
            })}
          </nav>
          <div className="side-foot">
            <button type="button" className="nav-btn" onClick={openServer}><Icon name="server" size={17} /><span>Server</span></button>
            <button type="button" className="nav-btn" onClick={openActivity}><Icon name="pulse" size={17} /><span>Activity & feedback</span></button>
            <button type="button" className="nav-btn" onClick={() => openSettings('general')}><Icon name="sliders" size={17} /><span>Settings</span></button>
            <button type="button" className="nav-btn" onClick={toggleTheme}><Icon name={theme === 'dark' ? 'sun' : 'moon'} size={17} /><span>{theme === 'dark' ? 'Light theme' : 'Dark theme'}</span></button>
          </div>
        </aside>

        <main className="main">
          {banner ? <div className="banner"><Icon name="info" size={16} /><span>{banner}</span><button type="button" className="icon-btn small" aria-label="Dismiss" onClick={() => setBanner('')}><Icon name="x" size={14} /></button></div> : null}
          <section className="transcript" ref={transcriptRef} onScroll={onTranscriptScroll} aria-live="polite">
            <div className="reading">
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
                  <h1 className="hero-title">What can VISION do for you?</h1>
                  <p className="hero-sub">A private local assistant for answers, files, live data and desktop actions — running on your machine first.</p>
                  <Composer hero value={draft} setValue={setDraft} onSubmit={() => streaming ? stopGeneration() : sendText(draft)} streaming={streaming} attachments={attachments} removeAttachment={removeAttachment} chooseAttachments={chooseAttachments} endpoint={endpoint} inputRef={inputRef} />
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
              ) : messages.map((message) => <Message key={message.key} message={message} handlers={messageHandlers} />)}
            </div>
          </section>
          {showJump && !empty ? <button type="button" className="jump" onClick={() => { stickRef.current = true; scrollBottom(true) }}><Icon name="arrowDown" size={14} />Jump to latest</button> : null}
          {!empty ? (
            <section className="composer-dock">
              <Composer value={draft} setValue={setDraft} onSubmit={() => streaming ? stopGeneration() : sendText(draft)} streaming={streaming} attachments={attachments} removeAttachment={removeAttachment} chooseAttachments={chooseAttachments} endpoint={endpoint} inputRef={inputRef} />
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
              <h3>{source.title || source.url || source.path || 'Source'}</h3>
              {source.locator || source.fetched_at || source.path ? <div className="source-meta">{source.locator || source.fetched_at || source.path}</div> : null}
              {source.snippet ? <p>{source.snippet}</p> : null}
            </article>
          )
        })}
      </aside>

      {overlay === 'settings' ? (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="sheet wide" role="dialog" aria-modal="true" aria-label="Settings">
            <div className="sheet-head"><h2>Settings</h2><button type="button" className="icon-btn" aria-label="Close settings" onClick={() => setOverlay('')}><Icon name="x" size={17} /></button></div>
            <div className="settings-layout">
              <nav className="settings-nav">
                {[['general', 'spark', 'General'], ['model', 'server', 'Model & server'], ['endpoint', 'globe', 'Endpoint'], ['privacy', 'shield', 'Privacy'], ['extensions', 'puzzle', 'Extensions'], ['about', 'info', 'About']].map(([id, icon, label]) => (
                  <button key={id} type="button" className={`settings-tab ${settingsTab === id ? 'active' : ''}`} onClick={() => setSettingsTab(id)}><Icon name={icon} size={16} />{label}</button>
                ))}
              </nav>
              <div className="settings-content">
                {!form ? <p className="empty-note">Loading settings…</p> : settingsTab === 'general' ? (
                  <><h3>General</h3><p className="section-copy">Appearance and default chat behavior.</p><div className="form-grid">
                    <div className="field"><label>Theme</label><select className="select" value={form.theme} onChange={(e) => { setForm({ ...form, theme: e.target.value }); setTheme(e.target.value) }}><option value="dark">Dark</option><option value="light">Light</option></select></div>
                    <div className="field"><label>Active project</label><select className="select" value={form.active_project_id} onChange={(e) => setForm({ ...form, active_project_id: e.target.value })}><option value="">No project</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name || p.title || p.id}</option>)}</select></div>
                    <div className="field"><label>Max extension tools / request</label><input className="input" type="number" min="0" max="128" value={form.max_extension_tools} onChange={(e) => setForm({ ...form, max_extension_tools: e.target.value })} /></div>
                    <label className="check-row"><input type="checkbox" checked={form.auto_start_local_server} onChange={(e) => setForm({ ...form, auto_start_local_server: e.target.checked })} />Start local server automatically</label>
                  </div></>
                ) : settingsTab === 'model' ? (
                  <><h3>Model & server</h3><p className="section-copy">The model and llama-server stay local. Server changes apply after restart.</p><div className="form-grid">
                    <div className="field full"><label>Model GGUF</label><div className="field-row"><input className="input" value={form.model_path} onChange={(e) => setForm({ ...form, model_path: e.target.value })} /><button type="button" className="btn" onClick={() => pickInto('model', 'model_path')}>Browse</button></div></div>
                    <div className="field full"><label>llama-server</label><div className="field-row"><input className="input" value={form.llama_server_path} onChange={(e) => setForm({ ...form, llama_server_path: e.target.value })} /><button type="button" className="btn" onClick={() => pickInto('server', 'llama_server_path')}>Browse</button></div></div>
                    <div className="field"><label>Local port</label><input className="input" type="number" min="1" max="65535" value={form.local_port} onChange={(e) => setForm({ ...form, local_port: e.target.value })} /></div>
                    <div className="field"><label>Context size</label><input className="input" type="number" min="1024" max="262144" step="1024" value={form.context_size} onChange={(e) => setForm({ ...form, context_size: e.target.value })} /></div>
                    <div className="field"><label>GPU layers</label><div className="range-row"><input type="range" min="0" max="120" value={form.gpu_layers} onChange={(e) => setForm({ ...form, gpu_layers: e.target.value })} /><span className="range-value">{form.gpu_layers}</span></div><small>Lower this if llama-server reports out of VRAM.</small></div>
                    <div className="field"><label>Local model name override</label><input className="input" placeholder="First model from /v1/models" value={form.local_model_override} onChange={(e) => setForm({ ...form, local_model_override: e.target.value })} /></div>
                  </div>
                  <div style={{ marginTop: 16 }}><StatusRows rows={serverRows(server, config.local_base_url).slice(0, 8)} /></div>
                  <div className="action-row">
                    <button type="button" className="btn" onClick={() => callApi('download_model')}>Download / repair model</button>
                    <button type="button" className="btn" onClick={() => callApi('download_llama')}>Download llama-server</button>
                    <button type="button" className="btn" onClick={() => callApi('check_model_update')}>Check model update</button>
                  </div>
                  {task ? <><p className="empty-note" style={{ marginTop: 12 }}>{task.message || 'Working…'}{task.percent != null ? ` ${task.percent}%` : ''}{task.total ? ` (${formatBytes(task.downloaded)} / ${formatBytes(task.total)})` : ''}</p>{task.percent != null ? <div className="progress-line"><i style={{ width: `${Math.max(0, Math.min(100, Number(task.percent) || 0))}%` }} /></div> : null}</> : null}
                  </>
                ) : settingsTab === 'endpoint' ? (
                  <><h3>Endpoint</h3><p className="section-copy">Auto uses the local endpoint first and only fails over after two failed health checks.</p><div className="form-grid">
                    <div className="field"><label>Endpoint mode</label><select className="select" value={form.endpoint_mode} onChange={(e) => setForm({ ...form, endpoint_mode: e.target.value })}><option value="auto">Auto</option><option value="local">Local only</option><option value="cloud">Cloud only</option></select></div>
                    <div className="field"><label>Remote model override</label><input className="input" placeholder="First remote model" value={form.remote_model_override} onChange={(e) => setForm({ ...form, remote_model_override: e.target.value })} /></div>
                    <div className="field full"><label>Remote base URL</label><input className="input" placeholder="https://example.com/v1" value={form.remote_base_url} onChange={(e) => setForm({ ...form, remote_base_url: e.target.value })} /><small>Use an OpenAI-compatible URL ending in /v1.</small></div>
                    <div className="field full"><label>Remote Bearer key</label><input className="input" type="password" autoComplete="new-password" placeholder={credentials.remote_api_key_stored ? 'Stored in Credential Manager' : 'Not stored'} value={form.remote_api_key} onChange={(e) => setForm({ ...form, remote_api_key: e.target.value })} /><small>Stored in Windows Credential Manager, never in config or logs.</small></div>
                    <label className="check-row"><input type="checkbox" disabled={!credentials.remote_api_key_stored} checked={form.clear_remote_api_key} onChange={(e) => setForm({ ...form, clear_remote_api_key: e.target.checked })} />Remove stored remote key on save</label>
                  </div></>
                ) : settingsTab === 'privacy' ? (
                  <><h3>Privacy & feedback</h3><p className="section-copy">Feedback stays in the local queue unless you explicitly opt in and upload it.</p><div className="form-grid">
                    <label className="check-row" style={{ gridColumn: '1 / -1' }}><input type="checkbox" checked={form.telemetry_opt_in} onChange={(e) => setForm({ ...form, telemetry_opt_in: e.target.checked })} />Allow feedback upload on exit</label>
                    <div className="field full"><label>Hugging Face token</label><input className="input" type="password" autoComplete="new-password" placeholder={credentials.hf_token_stored ? 'Stored in Credential Manager' : 'Not stored'} value={form.hf_token} onChange={(e) => setForm({ ...form, hf_token: e.target.value })} /><small>Needed only for gated downloads or feedback upload.</small></div>
                    <label className="check-row"><input type="checkbox" disabled={!credentials.hf_token_stored} checked={form.clear_hf_token} onChange={(e) => setForm({ ...form, clear_hf_token: e.target.checked })} />Remove stored HF token on save</label>
                  </div>
                  <div style={{ marginTop: 16 }}><StatusRows rows={[['App data', paths.app_data], ['Workspace', paths.workspace]]} /></div></>
                ) : settingsTab === 'extensions' ? (
                  <><h3>Extensions</h3><p className="section-copy">Skills are metadata-first. MCP and connector tools appear only after their exact toolset is trusted.</p>
                  <h4>Skills</h4>
                  {skills.length === 0 ? <p className="empty-note">No skills installed. Import a SKILL.md folder, Markdown file, .zip or ZIP-based .skill package.</p> : skills.map((skill) => (
                    <article className="extension-card" key={skill.name}><div><h5>{skill.name}</h5><p>{skill.description || 'No description'}</p><div className="extension-meta">{skill.files?.length || 0} files · {skill.scripts?.length || 0} scripts · SHA {String(skill.sha256 || '').slice(0, 10)}</div></div>
                      <label className="switch-row"><input type="checkbox" checked={form.enabled_skills.includes(skill.name)} onChange={(e) => setForm({ ...form, enabled_skills: e.target.checked ? [...form.enabled_skills, skill.name] : form.enabled_skills.filter((x) => x !== skill.name) })} />Enabled</label></article>
                  ))}
                  <div className="action-row"><button type="button" className="btn small" onClick={() => callApi('import_skill', false)}>Import skill file</button><button type="button" className="btn small" onClick={() => callApi('import_skill', true)}>Import skill folder</button></div>
                  <h4>MCP & connectors</h4>
                  {[['mcp', extensions.servers || []], ['connector', extensions.connectors || []]].flatMap(([kind, items]) => items.map((item) => {
                    const dynamic = (extensions.dynamic_tools || []).filter((name) => String(name).includes(item.id))
                    return (
                      <article className="extension-card" key={`${kind}-${item.id}`}><div><h5>{item.display_name || item.id}</h5><p>{item.url || [item.command, ...(item.args || [])].filter(Boolean).join(' ') || item.description || 'No endpoint/command shown'}</p>
                        <div className="extension-meta">{kind.toUpperCase()} · {item.transport || 'remote'} · {item.enabled ? 'enabled' : 'disabled'} · {item.toolset_sha256 ? 'trusted toolset' : 'not trusted'} · {dynamic.length} active tools</div>
                        <div className="extension-tools">{dynamic.length ? dynamic.join(', ') : 'Discover and trust the exact toolset before its tools are sent to the model.'}</div></div>
                        <button type="button" className="btn small" onClick={async () => { pushToast(`Discovering ${item.display_name || item.id}…`); await callApi('discover_extension', kind, item.id) }}>Discover / trust</button></article>
                    )
                  }))}
                  {(extensions.servers?.length || 0) + (extensions.connectors?.length || 0) === 0 ? <p className="empty-note">No MCP servers or connectors configured.</p> : null}
                  {extensions.messages?.length ? <p className="empty-note" style={{ whiteSpace: 'pre-wrap' }}>{extensions.messages.join('\n')}</p> : null}
                  <div className="action-row"><button type="button" className="btn small" onClick={() => callApi('refresh_extensions')}>Refresh trusted tools</button></div></>
                ) : (
                  <><h3>About</h3><p className="section-copy">Local-first desktop chat and tool runtime.</p>
                  <StatusRows rows={[['Product', 'VISION Desktop'], ['Version', appInfo.version || '—'], ['Build', appInfo.build || 'source'], ['Credits', 'VISION — built by SPIRIT ORG'], ['Frontend', 'Offline React interface in WebView2. Bundled locally; no CDN requests.']]} /></>
                )}
              </div>
            </div>
            <div className="sheet-foot"><button type="button" className="btn ghost" onClick={() => { setTheme(config.theme || 'dark'); setOverlay('') }}>Cancel</button><button type="button" className="btn primary" onClick={saveSettings}>Save settings</button></div>
          </section>
        </div>
      ) : null}

      {overlay === 'server' ? (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="sheet" role="dialog" aria-modal="true" aria-label="Server console">
            <div className="sheet-head"><h2>Server console</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => setOverlay('')}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body">
              <StatusRows rows={serverRows(server, config.local_base_url)} />
              <div className="action-row">
                <button type="button" className="btn primary" onClick={() => callApi('start_server')}>Start</button>
                <button type="button" className="btn" onClick={() => callApi('restart_server')}>Restart</button>
                <button type="button" className="btn danger" onClick={() => callApi('stop_server')}>Stop</button>
                <button type="button" className="btn ghost" onClick={openServer}>Refresh logs</button>
              </div>
              <h4 style={{ margin: '20px 0 9px', fontSize: 13 }}>Logs</h4>
              <pre className="server-log">{serverLogs.join('\n') || 'No server output yet.'}</pre>
            </div>
          </section>
        </div>
      ) : null}

      {overlay === 'activity' ? (
        <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) setOverlay('') }}>
          <section className="sheet wide" role="dialog" aria-modal="true" aria-label="Activity and feedback">
            <div className="sheet-head"><h2>Activity & feedback</h2><button type="button" className="icon-btn" aria-label="Close" onClick={() => setOverlay('')}><Icon name="x" size={17} /></button></div>
            <div className="sheet-body">
              <h4 style={{ margin: '0 0 9px', fontSize: 13 }}>Audit log</h4><pre className="server-log" style={{ height: 220 }}>{activity?.audit_text || 'No audit entries yet.'}</pre>
              <h4 style={{ margin: '20px 0 9px', fontSize: 13 }}>Feedback queue {activity?.feedback_count ? <span className="empty-note">({activity.feedback_count} queued)</span> : null}</h4>
              <pre className="server-log" style={{ height: 220 }}>{(activity?.feedback_rows || []).map((row) => JSON.stringify(row)).join('\n') || 'Feedback queue is empty.'}</pre>
              <div className="action-row">
                <button type="button" className="btn" onClick={async () => { const result = await callApi('export_feedback'); if (result?.ok) pushToast(`Feedback exported to ${result.path}`, 'success') }}>Export feedback</button>
                <button type="button" className="btn" onClick={() => callApi('upload_feedback')}>Upload now</button>
                <button type="button" className="btn danger" onClick={async () => { if (!clearArmed) { setClearArmed(true); return } await callApi('clear_feedback'); setClearArmed(false); openActivity() }}>{clearArmed ? 'Click again to clear' : 'Clear feedback'}</button>
              </div>
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
              <StatusRows rows={[['Model', server.model_exists ? `${formatBytes(server.model_size)} installed` : 'Missing'], ['Expected size', formatBytes(server.expected_model_size)], ['llama-server', server.binary || 'Not found'], ['Build', `${server.build ? `b${server.build}` : 'Unknown'} (${server.build_supported ? 'supported' : 'needs b11815+'})`], ['Server', server.state || 'Not checked']]} />
              <div className="field" style={{ marginTop: 16 }}><label>GPU layers</label><div className="range-row"><input type="range" min="0" max="120" value={setupGpu} onChange={(e) => setSetupGpu(e.target.value)} /><span className="range-value">{setupGpu}</span></div><small>99 offloads as much as possible. Lower it if the GPU runs out of VRAM.</small></div>
              {task ? <p className="empty-note" style={{ marginTop: 12 }}>{task.message || 'Working…'}{task.percent != null ? ` ${task.percent}%` : ''}</p> : null}
              <div className="action-row">
                <button type="button" className="btn" onClick={() => callApi('download_model')}>1. Download model</button>
                <button type="button" className="btn" onClick={() => callApi('download_llama')}>2. Download llama-server</button>
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
            <div className="sheet-body"><p className="section-copy" style={{ whiteSpace: 'pre-wrap' }}>{activeConfirm.message}</p><div className="detail-label">Details</div><pre className="json-block">{typeof activeConfirm.details === 'string' ? activeConfirm.details : JSON.stringify(activeConfirm.details || {}, null, 2)}</pre></div>
            <div className="sheet-foot"><button type="button" className="btn danger" onClick={() => resolveConfirm(false)}>{activeConfirm.denyLabel || 'Deny'}</button><button type="button" className="btn primary" autoFocus onClick={() => resolveConfirm(true)}>{activeConfirm.approveLabel || 'Allow'}</button></div>
          </section>
        </div>
      ) : null}

      <div className="toast-stack" aria-live="polite">{toasts.map((toast) => <div className={`toast ${toast.kind}`} key={toast.id}><Icon name={toast.kind === 'error' ? 'x' : toast.kind === 'success' ? 'check' : 'info'} size={16} /><span>{toast.message}</span></div>)}</div>
    </div>
  )
}
