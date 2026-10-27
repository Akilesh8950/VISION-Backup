export const demoProjects = [
  { id: 'demo-project-product', name: 'Product Research' },
  { id: 'demo-project-release', name: 'Release Planning' },
]

export const demoConversations = [
  { id: 'demo-1', title: 'Weather and system check', pinned: true, project_id: 'demo-project-product', updated_at: Date.now() / 1000 - 3600 },
  { id: 'demo-2', title: 'Quarterly values chart', pinned: false, project_id: 'demo-project-release', updated_at: Date.now() / 1000 - 90000 },
  { id: 'demo-3', title: 'Project source review', pinned: false, project_id: 'demo-project-product', updated_at: Date.now() / 1000 - 400000 },
]

export const demoLibrary = [
  {
    id: 'demo-library-notes', name: 'Research notes.md', extension: '.md', kind: 'text', mime_type: 'text/markdown',
    size: 8420, used_in_chats: 2, attachable: true, updated_at: new Date(Date.now() - 86400000).toISOString(), thumbnail: '',
  },
  {
    id: 'demo-library-data', name: 'quarterly-values.csv', extension: '.csv', kind: 'spreadsheet', mime_type: 'text/csv',
    size: 2910, used_in_chats: 1, attachable: true, updated_at: new Date(Date.now() - 172800000).toISOString(), thumbnail: '',
  },
  {
    id: 'demo-library-brief', name: 'Release brief.pdf', extension: '.pdf', kind: 'document', mime_type: 'application/pdf',
    size: 184320, used_in_chats: 0, attachable: false, updated_at: new Date(Date.now() - 259200000).toISOString(), thumbnail: '',
  },
]

export const demoMessages = [
  { key: 'demo-u1', role: 'user', content: 'Show system status, then draw a clean bar chart of 1, 4, 9, 16.' },
  {
    key: 'demo-a1', role: 'assistant',
    reasoning: 'I gathered the local system metrics first, then rendered the requested values as a bar chart so the growth is easy to compare.',
    content: 'Here is the current machine snapshot and the chart. The values grow quadratically, doubling the gap at each step.',
    tools: [
      {
        id: 'demo-sys', name: 'system_status', args: {}, status: 'success', durationMs: 184, summary: 'CPU, memory, disk and GPU status collected.',
        raw: { cpu_percent: 18.4, cpu_count: 16, ram_percent: 46.2, ram_used_gb: 14.8, ram_total_gb: 32, disk_used_percent: 61.4, disk_free_gb: 214.8, disk_total_gb: 552, disk_root: 'C:', gpu: { devices: [{ name: 'GPU', utilization_percent: 38, vram_used_mb: 5900, vram_total_mb: 8192 }] } },
      },
      {
        id: 'demo-chart', name: 'ui_chart', args: { title: 'Squares', chart_type: 'bar', labels: ['1', '2', '3', '4'], values: [1, 4, 9, 16] },
        status: 'success', durationMs: 42, summary: 'Rendered chart.',
        genui: { kind: 'chart', title: 'Squares', chart_type: 'bar', labels: ['1', '2', '3', '4'], values: [1, 4, 9, 16] },
      },
    ],
    sources: [{ source_id: 'S1', title: 'Local system telemetry', snippet: 'Collected on this device at the time of the request.', fetched_at: 'Just now' }],
  },
  { key: 'demo-u2', role: 'user', content: 'Put the quarterly numbers in a table and summarize the release plan.' },
  {
    key: 'demo-a2', role: 'assistant', content: 'Quarterly values are below, followed by the compact release timeline.',
    tools: [
      {
        id: 'demo-table', name: 'ui_table', args: { title: 'Quarterly values' }, status: 'success', durationMs: 31, summary: 'Rendered table.',
        genui: { kind: 'table', title: 'Quarterly values', columns: ['Quarter', 'Value', 'Change'], rows: [['Q1', 12, '—'], ['Q2', 19, '+7'], ['Q3', 16, '−3'], ['Q4', 24, '+8']] },
      },
      {
        id: 'demo-time', name: 'ui_timeline', args: { title: 'Release timeline' }, status: 'success', durationMs: 28, summary: 'Rendered timeline.',
        genui: {
          kind: 'timeline', title: 'Release timeline', events: [
            { time: '09:00', title: 'Baseline', description: 'Tests and benchmarks recorded before the UI swap.' },
            { time: '11:30', title: 'React shell', description: 'Premium offline frontend wired to the unchanged Python services.' },
            { time: '14:00', title: 'Windows smoke', description: 'WebView2, native dialogs and GGUF inference verified on device.' },
          ],
        },
      },
    ],
  },
  { key: 'demo-u3', role: 'user', content: 'Show the greeting helper cleanup as a code diff.' },
  {
    key: 'demo-a3', role: 'assistant', content: 'The cleanup replaces string concatenation with an f-string and trims the input name.',
    tools: [
      {
        id: 'demo-diff', name: 'ui_code_diff', args: { filename: 'src/greeting.py' }, status: 'success', durationMs: 24, summary: 'Rendered side-by-side diff.',
        genui: {
          kind: 'diff', filename: 'src/greeting.py',
          before: 'def greet(name):\n    return "Hello, " + name\n',
          after: 'def greet(name):\n    return f"Hello, {name.strip()}"\n',
        },
      },
    ],
  },
]

export const demoUpdates = {
  enabled: true,
  offline: false,
  channel: 'stable',
  auto_check: true,
  last_checked: new Date().toISOString(),
  launcher_managed: false,
  channels: {
    app: { channel: 'app', installed_version: '1.0.0', available_version: '', status: 'up_to_date', status_label: 'Up to date', last_checked: new Date().toISOString(), progress: null, notes: 'VISION 1.0.0 is the current release.', error: '', previous_version: '' },
    model: { channel: 'model', installed_version: '46956dae5e21', available_version: '', status: 'up_to_date', status_label: 'Up to date', last_checked: new Date().toISOString(), progress: null, notes: '', error: '', previous_version: '' },
    config: { channel: 'config', installed_version: '1.0.0', available_version: '', status: 'up_to_date', status_label: 'Up to date', last_checked: new Date().toISOString(), progress: null, notes: '', error: '', previous_version: '' },
  },
  ui_strings: {},
  installed_model_sha: '46956dae5e217795a41d7bd40b72962ad7b6fde5',
  model_updated_at: new Date(Date.now() - 3 * 86400000).toISOString(),
}

export const demoServer = {
  state: 'Healthy', running: true, healthy: true, failures: 0, internet: 'online',
  model_exists: true, model_size: 5629108640, expected_model_size: 5629108640,
  model_path: 'vision.gguf', binary: 'llama-server.exe', binary_checked: true,
  build: 11819, build_supported: true, minimum_build: 11815,
}
