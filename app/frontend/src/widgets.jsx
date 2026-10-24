import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Chart, registerables } from 'chart.js'
import { callOptional, isNative } from './bridge.js'
import { Icon } from './icons.jsx'

Chart.register(...registerables)

const PALETTE = ['#6ea8fe', '#e08a63', '#7fb069', '#e3b505', '#9b8cff', '#5ec8c0', '#d979a8', '#8a93a3']

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}
function safeFilename(value, extension) {
  const stem = String(value || 'vision-widget').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64) || 'vision-widget'
  return `${stem}.${extension}`
}
function csvCell(value) {
  const text = value == null ? '' : String(value)
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}
function toCsv(rows) { return rows.map((row) => row.map(csvCell).join(',')).join('\n') + '\n' }

async function saveData(filename, data, encoding, mime) {
  if (isNative()) {
    const result = await callOptional('save_export', null, { filename, data, encoding, mime })
    if (result) return result
  }
  const bytes = encoding === 'base64' ? Uint8Array.from(atob(data), (c) => c.charCodeAt(0)) : new TextEncoder().encode(data)
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }))
  const a = document.createElement('a')
  a.href = url; a.download = filename; document.body.append(a); a.click(); a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  return { ok: true }
}
const saveCsv = (title, rows) => saveData(safeFilename(title, 'csv'), toCsv(rows), 'text', 'text/csv;charset=utf-8')
const saveCanvas = (canvas, title) => saveData(safeFilename(title, 'png'), canvas.toDataURL('image/png').split(',')[1], 'base64', 'image/png')

function createCanvas(width, height) {
  const scale = Math.min(window.devicePixelRatio || 1, 2)
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale)
  const ctx = canvas.getContext('2d')
  ctx.scale(scale, scale)
  ctx.fillStyle = cssVar('--surface', '#1c1c1a'); ctx.fillRect(0, 0, width, height)
  return { canvas, ctx }
}
function wrapLines(ctx, text, maxWidth, maxLines = 3) {
  const words = String(text || '').split(/\s+/).filter(Boolean)
  const lines = []; let line = ''
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word
    if (ctx.measureText(candidate).width <= maxWidth || !line) line = candidate
    else { lines.push(line); line = word; if (lines.length >= maxLines) break }
  }
  if (line && lines.length < maxLines) lines.push(line)
  return lines.length ? lines : ['']
}
function drawBlock(ctx, text, x, y, maxWidth, lineHeight, maxLines = 3) {
  const lines = wrapLines(ctx, text, maxWidth, maxLines)
  lines.forEach((line, i) => ctx.fillText(line, x, y + i * lineHeight))
  return lines.length * lineHeight
}

function WidgetShell({ title, onPng, onCsv, children }) {
  const [open, setOpen] = useState(false)
  return (
    <section className="widget">
      <div className="widget-head">
        <div className="widget-title">{title || 'Result'}</div>
        <div className="widget-menu-wrap">
          <button type="button" className="icon-btn small" title="Export" aria-label="Export widget" onClick={() => setOpen((v) => !v)}>
            <Icon name="download" size={15} />
          </button>
          {open && (
            <div className="export-menu">
              <button type="button" onClick={async () => { setOpen(false); await onPng?.() }}>Export PNG</button>
              <button type="button" onClick={async () => { setOpen(false); await onCsv?.() }}>Export CSV</button>
            </div>
          )}
        </div>
      </div>
      <div className="widget-body">{children}</div>
    </section>
  )
}

function TableWidget(payload) {
  const columns = Array.isArray(payload.columns) ? payload.columns : []
  const [sort, setSort] = useState({ index: -1, dir: 1 })
  const rows = useMemo(() => {
    const data = [...(Array.isArray(payload.rows) ? payload.rows : [])]
    if (sort.index < 0) return data
    return data.sort((a, b) => {
      const av = a[sort.index]; const bv = b[sort.index]
      const an = Number(av); const bn = Number(bv)
      const result = Number.isFinite(an) && Number.isFinite(bn) ? an - bn : String(av ?? '').localeCompare(String(bv ?? ''), undefined, { numeric: true })
      return result * sort.dir
    })
  }, [payload.rows, sort])

  const png = () => {
    const shown = rows.slice(0, 100)
    const colW = Math.max(130, Math.min(240, Math.floor(1100 / Math.max(columns.length, 1))))
    const width = Math.max(660, columns.length * colW + 32); const height = 80 + (shown.length + 1) * 34
    const { canvas, ctx } = createCanvas(width, height)
    ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 20px Segoe UI, sans-serif'; ctx.fillText(payload.title || 'Table', 16, 32)
    ctx.font = '600 11px Segoe UI, sans-serif'; ctx.fillStyle = cssVar('--muted', '#aaa49a')
    columns.forEach((c, i) => ctx.fillText(String(c).toUpperCase(), 16 + i * colW, 64))
    ctx.font = '13px Segoe UI, sans-serif'
    shown.forEach((row, r) => {
      const y = 96 + r * 34
      ctx.strokeStyle = cssVar('--border', 'rgba(255,255,255,.09)'); ctx.beginPath(); ctx.moveTo(16, y - 21); ctx.lineTo(width - 16, y - 21); ctx.stroke()
      ctx.fillStyle = cssVar('--text', '#f3f0e8')
      columns.forEach((_, i) => { const v = String(row[i] ?? ''); ctx.fillText(v.length > 29 ? `${v.slice(0, 28)}…` : v, 16 + i * colW, y) })
    })
    return saveCanvas(canvas, payload.title || 'table')
  }

  return (
    <WidgetShell title={payload.title || 'Table'} onCsv={() => saveCsv(payload.title || 'table', [columns, ...rows])} onPng={png}>
      <div className="table-scroll"><table className="data-table">
        <thead><tr>{columns.map((column, i) => (
          <th key={i} aria-sort={sort.index === i ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}>
            <button type="button" className="sort-btn" onClick={() => setSort((s) => s.index === i ? { index: i, dir: -s.dir } : { index: i, dir: 1 })} title={`Sort by ${String(column)}`}>
              <span>{String(column)}</span><span aria-hidden="true">{sort.index === i ? (sort.dir === 1 ? '↑' : '↓') : ''}</span>
            </button>
          </th>))}
        </tr></thead>
        <tbody>{rows.map((row, r) => <tr key={r}>{columns.map((_, c) => <td key={c}>{row[c] == null ? '' : String(row[c])}</td>)}</tr>)}</tbody>
      </table></div>
    </WidgetShell>
  )
}

function ChartWidget(payload) {
  const canvasRef = useRef(null)
  const chartRef = useRef(null)
  const [themeVersion, setThemeVersion] = useState(0)

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeVersion((value) => value + 1))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  const labels = Array.isArray(payload.labels) ? payload.labels.map(String) : []
  const values = Array.isArray(payload.values) ? payload.values.map(Number) : []
  const type = ['bar', 'line', 'pie', 'scatter'].includes(payload.chart_type) ? payload.chart_type : 'bar'

  useEffect(() => {
    if (!canvasRef.current) return undefined
    chartRef.current?.destroy()
    const muted = cssVar('--muted', '#aaa49a')
    const border = cssVar('--border', 'rgba(255,255,255,.09)')
    const solid = '#6ea8fe'
    const data = type === 'scatter' ? values.map((v, i) => ({ x: Number(labels[i]) || i + 1, y: v })) : values
    chartRef.current = new Chart(canvasRef.current, {
      type,
      data: {
        labels: type === 'scatter' ? undefined : labels,
        datasets: [{
          label: payload.title || 'Values', data,
          backgroundColor: type === 'pie' ? labels.map((_, i) => PALETTE[i % PALETTE.length]) : type === 'line' ? 'rgba(110,168,254,.14)' : solid,
          borderColor: solid, borderWidth: 2, borderRadius: type === 'bar' ? 7 : 0,
          fill: type === 'line', tension: type === 'line' ? 0.32 : 0,
          pointRadius: type === 'scatter' ? 4 : 3, pointBackgroundColor: solid,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? false : { duration: 260, easing: 'easeOutCubic' },
        plugins: {
          legend: { display: type === 'pie', position: 'bottom', labels: { color: muted, boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { family: 'Segoe UI, sans-serif', size: 11 } } },
          tooltip: { backgroundColor: cssVar('--surface-2', '#242422'), titleColor: cssVar('--text', '#f3f0e8'), bodyColor: muted, borderColor: border, borderWidth: 1, padding: 10 },
        },
        scales: type === 'pie' ? {} : {
          x: { grid: { display: false }, border: { color: border }, ticks: { color: muted, font: { size: 11 } } },
          y: { grid: { color: 'rgba(170,164,154,.09)' }, border: { display: false }, ticks: { color: muted, font: { size: 11 } } },
        },
      },
    })
    return () => { chartRef.current?.destroy(); chartRef.current = null }
  }, [JSON.stringify(payload), themeVersion])

  return (
    <WidgetShell title={payload.title || 'Chart'}
      onCsv={() => saveCsv(payload.title || 'chart', [['label', 'value'], ...labels.map((label, i) => [label, values[i]])])}
      onPng={() => chartRef.current ? saveData(safeFilename(payload.title || 'chart', 'png'), chartRef.current.toBase64Image('image/png', 1).split(',')[1], 'base64', 'image/png') : Promise.resolve()}>
      <div className="chart-wrap"><canvas ref={canvasRef} /></div>
    </WidgetShell>
  )
}

function CardsWidget(payload) {
  const cards = Array.isArray(payload.cards) ? payload.cards : []
  const png = () => {
    const cols = Math.min(3, Math.max(1, cards.length)); const cardW = 250; const cardH = 150
    const rows = Math.ceil(cards.length / cols); const width = cols * cardW + (cols + 1) * 16; const height = 64 + rows * (cardH + 16)
    const { canvas, ctx } = createCanvas(width, height)
    ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 20px Segoe UI, sans-serif'; ctx.fillText(payload.title || 'Cards', 16, 34)
    cards.forEach((item, i) => {
      const x = 16 + (i % cols) * (cardW + 16); const y = 54 + Math.floor(i / cols) * (cardH + 16)
      ctx.fillStyle = cssVar('--surface-2', '#242422'); ctx.beginPath(); ctx.roundRect(x, y, cardW, cardH, 12); ctx.fill()
      ctx.strokeStyle = cssVar('--border', 'rgba(255,255,255,.09)'); ctx.stroke()
      ctx.fillStyle = cssVar('--muted', '#aaa49a'); ctx.font = '600 11px Segoe UI, sans-serif'; ctx.fillText(String(item.title || '').toUpperCase(), x + 14, y + 25)
      ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 24px Segoe UI, sans-serif'; ctx.fillText(String(item.value || ''), x + 14, y + 59)
      ctx.font = '13px Segoe UI, sans-serif'; ctx.fillStyle = cssVar('--muted', '#aaa49a'); drawBlock(ctx, item.body || item.footer || '', x + 14, y + 85, cardW - 28, 18, 3)
    })
    return saveCanvas(canvas, payload.title || 'cards')
  }
  return (
    <WidgetShell title={payload.title || 'Cards'} onCsv={() => saveCsv(payload.title || 'cards', [['title', 'value', 'body', 'footer'], ...cards.map((c) => [c.title, c.value, c.body, c.footer])])} onPng={png}>
      <div className="cards-grid">{cards.map((item, i) => (
        <article className="info-card" key={i}>
          <h4>{item.title || ''}</h4>
          {item.value ? <div className="card-value">{item.value}</div> : null}
          {item.body ? <p>{item.body}</p> : null}
          {item.footer ? <footer>{item.footer}</footer> : null}
        </article>))}
      </div>
    </WidgetShell>
  )
}

function TimelineWidget(payload) {
  const events = Array.isArray(payload.events) ? payload.events : []
  const png = () => {
    const width = 860; const height = 78 + events.slice(0, 50).length * 82
    const { canvas, ctx } = createCanvas(width, height)
    ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 20px Segoe UI, sans-serif'; ctx.fillText(payload.title || 'Timeline', 20, 34)
    ctx.strokeStyle = cssVar('--border', 'rgba(255,255,255,.12)'); ctx.beginPath(); ctx.moveTo(34, 56); ctx.lineTo(34, height - 20); ctx.stroke()
    events.slice(0, 50).forEach((event, i) => {
      const y = 78 + i * 82
      ctx.fillStyle = '#6ea8fe'; ctx.beginPath(); ctx.arc(34, y - 5, 5, 0, Math.PI * 2); ctx.fill()
      ctx.font = '700 11px Segoe UI, sans-serif'; ctx.fillText(String(event.time || ''), 54, y - 8)
      ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 15px Segoe UI, sans-serif'; ctx.fillText(String(event.title || ''), 54, y + 13)
      ctx.fillStyle = cssVar('--muted', '#aaa49a'); ctx.font = '13px Segoe UI, sans-serif'; drawBlock(ctx, event.description || '', 54, y + 34, width - 80, 17, 2)
    })
    return saveCanvas(canvas, payload.title || 'timeline')
  }
  return (
    <WidgetShell title={payload.title || 'Timeline'} onCsv={() => saveCsv(payload.title || 'timeline', [['time', 'title', 'description'], ...events.map((e) => [e.time, e.title, e.description])])} onPng={png}>
      <div className="timeline">{events.map((event, i) => (
        <article className="timeline-item" key={i}><div className="timeline-time">{event.time || ''}</div><h4>{event.title || ''}</h4>{event.description ? <p>{event.description}</p> : null}</article>))}
      </div>
    </WidgetShell>
  )
}

function diffRows(beforeLines, afterLines) {
  let start = 0
  while (start < beforeLines.length && start < afterLines.length && beforeLines[start] === afterLines[start]) start += 1
  let bEnd = beforeLines.length; let aEnd = afterLines.length
  while (bEnd > start && aEnd > start && beforeLines[bEnd - 1] === afterLines[aEnd - 1]) { bEnd -= 1; aEnd -= 1 }
  const rows = []
  for (let i = 0; i < start; i += 1) rows.push({ status: 'same', beforeNo: i + 1, afterNo: i + 1, before: beforeLines[i], after: afterLines[i] })
  const midB = beforeLines.slice(start, bEnd); const midA = afterLines.slice(start, aEnd)
  for (let i = 0; i < Math.max(midB.length, midA.length); i += 1) {
    rows.push({
      status: midB[i] !== undefined && midA[i] !== undefined ? 'changed' : midB[i] !== undefined ? 'removed' : 'added',
      beforeNo: midB[i] !== undefined ? start + i + 1 : '', afterNo: midA[i] !== undefined ? start + i + 1 : '',
      before: midB[i] ?? '', after: midA[i] ?? '',
    })
  }
  for (let i = bEnd, j = aEnd; i < beforeLines.length && j < afterLines.length; i += 1, j += 1) {
    rows.push({ status: 'same', beforeNo: i + 1, afterNo: j + 1, before: beforeLines[i], after: afterLines[j] })
  }
  return rows
}

function DiffWidget(payload) {
  const rows = useMemo(() => diffRows(String(payload.before || '').split('\n').slice(0, 1200), String(payload.after || '').split('\n').slice(0, 1200)), [payload.before, payload.after])
  const title = payload.filename || payload.title || 'Diff'
  const png = () => {
    const shown = rows.slice(0, 120); const width = 1120; const height = 74 + shown.length * 24
    const { canvas, ctx } = createCanvas(width, height)
    ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 19px Segoe UI, sans-serif'; ctx.fillText(title, 16, 32)
    ctx.font = '12px Cascadia Code, Consolas, monospace'
    shown.forEach((row, i) => {
      const y = 60 + i * 24
      if (row.status !== 'same') { ctx.fillStyle = row.before ? 'rgba(224,108,97,.12)' : 'rgba(90,180,120,.12)'; ctx.fillRect(12, y - 15, width - 24, 21) }
      ctx.fillStyle = cssVar('--faint', '#767168'); ctx.fillText(String(row.beforeNo).padStart(4, ' '), 16, y); ctx.fillText(String(row.afterNo).padStart(4, ' '), 566, y)
      ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.fillText(String(row.before).slice(0, 72), 70, y); ctx.fillText(String(row.after).slice(0, 72), 620, y)
    })
    return saveCanvas(canvas, title)
  }
  return (
    <WidgetShell title={title} onCsv={() => saveCsv(title, [['status', 'before_line', 'before', 'after_line', 'after'], ...rows.map((r) => [r.status, r.beforeNo, r.before, r.afterNo, r.after])])} onPng={png}>
      <div className="diff-wrap"><table className="diff-table">
        <thead><tr><th /><th>Before</th><th /><th>After</th></tr></thead>
        <tbody>{rows.slice(0, 500).map((row, i) => (
          <tr key={i}><td className="line-no">{row.beforeNo}</td><td className={row.status !== 'same' && row.before ? 'removed' : ''}>{row.before}</td><td className="line-no">{row.afterNo}</td><td className={row.status !== 'same' && row.after ? 'added' : ''}>{row.after}</td></tr>))}
        </tbody>
      </table></div>
      {rows.length > 500 ? <p className="empty-note">Showing the first 500 of {rows.length} diff rows.</p> : null}
    </WidgetShell>
  )
}

export function GenWidget({ payload }) {
  if (!payload || typeof payload !== 'object') return null
  switch (payload.kind) {
    case 'table': return <TableWidget {...payload} />
    case 'chart': return <ChartWidget {...payload} />
    case 'cards': return <CardsWidget {...payload} />
    case 'timeline': return <TimelineWidget {...payload} />
    case 'diff': return <DiffWidget {...payload} />
    default:
      return (
        <WidgetShell title={payload.title || 'Result'} onCsv={() => saveCsv(payload.title || 'result', [['value'], [JSON.stringify(payload)]])} onPng={() => {
          const { canvas, ctx } = createCanvas(760, 220)
          ctx.fillStyle = cssVar('--text', '#f3f0e8'); ctx.font = '700 20px Segoe UI, sans-serif'; ctx.fillText(payload.title || 'Result', 20, 40)
          ctx.font = '13px Segoe UI, sans-serif'; ctx.fillStyle = cssVar('--muted', '#aaa49a'); drawBlock(ctx, JSON.stringify(payload), 20, 74, 720, 19, 6)
          return saveCanvas(canvas, payload.title || 'result')
        }}><pre className="json-block">{JSON.stringify(payload, null, 2)}</pre></WidgetShell>
      )
  }
}

export function systemStatusPayload(result) {
  const raw = result?.raw && typeof result.raw === 'object' ? result.raw : null
  if (!raw || result?.name !== 'system_status') return null
  const gpuDevices = Array.isArray(raw.gpu?.devices) ? raw.gpu.devices : []
  const gpuText = gpuDevices.length ? gpuDevices.map((g) => `${g.name}: ${Math.round(g.vram_used_mb || 0)} / ${Math.round(g.vram_total_mb || 0)} MB VRAM`).join('\n') : 'No NVIDIA GPU telemetry available'
  return {
    kind: 'cards', title: 'System status', cards: [
      { title: 'CPU', value: `${Number(raw.cpu_percent || 0).toFixed(1)}%`, body: `${raw.cpu_count || '—'} logical processors` },
      { title: 'RAM', value: `${Number(raw.ram_percent || 0).toFixed(1)}%`, body: `${raw.ram_used_gb || 0} GB used of ${raw.ram_total_gb || 0} GB` },
      { title: 'Disk', value: `${Number(raw.disk_used_percent || 0).toFixed(1)}%`, body: `${raw.disk_free_gb || 0} GB free of ${raw.disk_total_gb || 0} GB on ${raw.disk_root || ''}` },
      { title: 'GPU', value: gpuDevices.length ? `${Number(gpuDevices[0].utilization_percent || 0).toFixed(0)}%` : '—', body: gpuText },
    ],
  }
}
