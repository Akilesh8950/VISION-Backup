import React from 'react'

const P = {
  logo: <><path d="M4 5.5 12 20 20 5.5" /><path d="M8.2 5.5 12 13.2 15.8 5.5" /></>,
  panel: <><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="M9.2 4.8v14.4" /></>,
  compose: <><path d="M12 20h7.2" /><path d="M16.8 3.8a2.4 2.4 0 0 1 3.4 3.4L8.2 19.2 3.8 20.2l1-4.4L16.8 3.8Z" /></>,
  search: <><circle cx="11" cy="11" r="6.4" /><path d="m20 20-4.2-4.2" /></>,
  message: <path d="M20.5 11.8a7.9 7.9 0 0 1-8 7.7H4l2.2-2.8a7.9 7.9 0 1 1 14.3-4.9Z" />,
  pin: <><path d="M9 4h6l-1 6.2 3 2.8v2H7v-2l3-2.8L9 4Z" /><path d="M12 15v5" /></>,
  pencil: <><path d="M16.9 4.1a2.2 2.2 0 0 1 3 3L8.3 18.7 4 20l1.3-4.3L16.9 4.1Z" /><path d="m14.6 6.4 3 3" /></>,
  trash: <><path d="M4.5 6.8h15M9.5 6.5v-2h5v2M6.8 6.8l.8 12.2a1.8 1.8 0 0 0 1.8 1.7h5.2a1.8 1.8 0 0 0 1.8-1.7l.8-12.2" /><path d="M10 10.8v5.4M14 10.8v5.4" /></>,
  dots: <><circle cx="5" cy="12" r=".7" fill="currentColor" /><circle cx="12" cy="12" r=".7" fill="currentColor" /><circle cx="19" cy="12" r=".7" fill="currentColor" /></>,
  sliders: <><path d="M5 7h8M17 7h2M5 17h3M12 17h7" /><circle cx="15" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></>,
  server: <><rect x="3.5" y="4" width="17" height="6.8" rx="2" /><rect x="3.5" y="13.2" width="17" height="6.8" rx="2" /><path d="M7 7.4h.01M7 16.6h.01" /></>,
  pulse: <path d="M3 12h4l2.4-6 4.2 12 2.4-6h5" />,
  sun: <><circle cx="12" cy="12" r="3.8" /><path d="M12 2.8v2.1M12 19.1v2.1M2.8 12h2.1M19.1 12h2.1M5.5 5.5l1.5 1.5M17 17l1.5 1.5M18.5 5.5 17 7M7 17l-1.5 1.5" /></>,
  moon: <path d="M20 14.6A8.2 8.2 0 1 1 9.4 4a6.7 6.7 0 0 0 10.6 10.6Z" />,
  plus: <path d="M12 5v14M5 12h14" />,
  attach: <path d="m20.2 12.4-8 8a5.1 5.1 0 0 1-7.2-7.2l8.5-8.5a3.4 3.4 0 0 1 4.8 4.8l-8.2 8.2a1.7 1.7 0 0 1-2.4-2.4l7.4-7.4" />,
  arrowUp: <><path d="M12 19V5.5" /><path d="m6.2 11 5.8-5.8 5.8 5.8" /></>,
  stop: <rect x="7.2" y="7.2" width="9.6" height="9.6" rx="2" />,
  x: <path d="m5.5 5.5 13 13M18.5 5.5l-13 13" />,
  minus: <path d="M5 12h14" />,
  expand: <rect x="5.2" y="5.2" width="13.6" height="13.6" rx="2" />,
  chevR: <path d="m9 5 7 7-7 7" />,
  chevD: <path d="m5 9 7 7 7-7" />,
  arrowDown: <><path d="M12 4.5v15" /><path d="m6 13.8 6 6 6-6" /></>,
  copy: <><rect x="9" y="9" width="11.5" height="11.5" rx="2.4" /><path d="M5.5 15H5A1.8 1.8 0 0 1 3.2 13.2V5A1.8 1.8 0 0 1 5 3.2h8.2A1.8 1.8 0 0 1 15 5v.5" /></>,
  refresh: <><path d="M19.5 12a7.5 7.5 0 1 1-2.1-5.3" /><path d="M19.7 3.4v4.8h-4.8" /></>,
  thumb: <path d="M7 11.2v8.3H4.2v-8.3H7Zm0 0 4-6.9c1.4 0 2.2 1 2 2.3l-.7 4.6h6.4a1.9 1.9 0 0 1 1.9 2.3l-1.1 4.7a1.9 1.9 0 0 1-1.9 1.6H9.9a2.9 2.9 0 0 1-2.3-1.4L7 17.2v-6Z" />,
  spark: <path d="M12 3.5c.8 4.7 2 5.9 6.7 6.7-4.7.8-5.9 2-6.7 6.7-.8-4.7-2-5.9-6.7-6.7 4.7-.8 5.9-2 6.7-6.7Z" />,
  cloud: <path d="M7.2 18a4.3 4.3 0 1 1 .6-8.5A5.8 5.8 0 0 1 19 11.3 3.7 3.7 0 0 1 18.4 18H7.2Z" />,
  cpu: <><rect x="7" y="7" width="10" height="10" rx="2" /><path d="M10 2.8V7M14 2.8V7M10 17v4.2M14 17v4.2M2.8 10H7M2.8 14H7M17 10h4.2M17 14h4.2" /></>,
  chart: <><path d="M4 20h17" /><path d="M7 20v-6M12 20V5M17 20v-9" /></>,
  appWindow: <><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="M3.5 9h17M6.8 6.8h.01M10 6.8h.01" /></>,
  database: <><ellipse cx="12" cy="5.4" rx="7.7" ry="2.9" /><path d="M4.3 5.4v13c0 1.6 3.5 2.9 7.7 2.9s7.7-1.3 7.7-2.9v-13M4.3 11.9c0 1.6 3.5 2.9 7.7 2.9s7.7-1.3 7.7-2.9" /></>,
  code: <><path d="m8.2 7-4.7 5 4.7 5M15.8 7l4.7 5-4.7 5" /><path d="m13.2 4-2.8 16" /></>,
  folder: <path d="M3.5 7A1.9 1.9 0 0 1 5.4 5.1h4.7l2 2.4h6.5A1.9 1.9 0 0 1 20.5 9.4v7.9a2.2 2.2 0 0 1-2.2 2.2H5.7a2.2 2.2 0 0 1-2.2-2.2V7Z" />,
  bookmark: <path d="M6.5 3.5h11V20L12 15.8 6.5 20V3.5Z" />,
  layout: <><rect x="3.5" y="3.5" width="17" height="17" rx="3" /><path d="M3.5 9.2h17M9.2 20.5V9.2" /></>,
  puzzle: <path d="M10 4.2a1.9 1.9 0 1 1 3.8 0h5v5a1.9 1.9 0 1 0 0 3.8v5h-5a1.9 1.9 0 1 0-3.8 0h-5v-5A1.9 1.9 0 1 1 5 9.2v-5h5Z" />,
  download: <><path d="M12 3.5v11M7.2 10 12 14.8 16.8 10" /><path d="M4.5 20.5h15" /></>,
  external: <><path d="M13.8 4h6.2v6.2" /><path d="M20 4 11 13" /><path d="M19 13.8v5a1.7 1.7 0 0 1-1.7 1.7H5.2A1.7 1.7 0 0 1 3.5 18.8V6.7A1.7 1.7 0 0 1 5.2 5H11" /></>,
  shield: <><path d="M12 3.2 5.2 5.8v5.1c0 4.4 2.8 7.5 6.8 9.6 4-2.1 6.8-5.2 6.8-9.6V5.8L12 3.2Z" /><path d="m9.2 11.8 2 2 3.8-4" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v4.8M12 8.2h.01" /></>,
  check: <path d="m4.8 12.5 4.7 4.7L19.2 7.5" />,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  file: <><path d="M7 3.5h7L19 8.6V20a1.7 1.7 0 0 1-1.7 1.7H7A1.7 1.7 0 0 1 5.3 20V5.2A1.7 1.7 0 0 1 7 3.5Z" /><path d="M13.8 3.8v5h5" /></>,
  globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.3 2.5 3.4 5.3 3.4 8.5s-1.1 6-3.4 8.5c-2.3-2.5-3.4-5.3-3.4-8.5S9.7 6 12 3.5Z" /></>,
  book: <><path d="M5 4.5A1.6 1.6 0 0 1 6.6 3H19v15H6.8A1.8 1.8 0 0 0 5 19.8V4.5Z" /><path d="M5 19.5A1.6 1.6 0 0 1 6.6 18H19v3H6.6A1.6 1.6 0 0 1 5 19.5Z" /></>,
  terminal: <><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="m7 9 3 3-3 3M12.5 15.5H17" /></>,
  layers: <><path d="m12 3.8 8 4.5-8 4.5-8-4.5 8-4.5Z" /><path d="m4.5 13 7.5 4.2L19.5 13" /></>,
}

export function Icon({ name, size = 18, strokeWidth = 1.7, className = '' }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {P[name] || P.spark}
    </svg>
  )
}

export function LogoMark({ size = 30 }) {
  return (
    <span className="logo-mark" style={{ width: size, height: size }} aria-hidden="true">
      <svg width={size * 0.66} height={size * 0.66} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
        {P.logo}
      </svg>
    </span>
  )
}

export const TOOL_ICONS = {
  data: 'database', code: 'code', files: 'folder', memory: 'bookmark', ui: 'layout', windows: 'appWindow', extension: 'puzzle',
}

const TOOL_CATEGORY = new Map(Object.entries({
  web_search: 'data', get_news: 'data', get_weather: 'data', get_stock_price: 'data', get_crypto_price: 'data', lookup_docs: 'data', read_url: 'data',
  run_python: 'code', search_code: 'code',
  read_file: 'files', write_file: 'files', list_files: 'files',
  memory_get: 'memory', memory_set: 'memory', skill_load: 'memory',
  ui_table: 'ui', ui_chart: 'ui', ui_cards: 'ui', ui_timeline: 'ui', ui_code_diff: 'ui',
  list_running_apps: 'windows', open_app: 'windows', focus_window: 'windows', open_path: 'windows', open_url: 'windows',
  clipboard_get: 'windows', clipboard_set: 'windows', system_status: 'windows', set_timer: 'windows',
}))

export function toolCategory(name) { return TOOL_CATEGORY.get(name) || 'extension' }
