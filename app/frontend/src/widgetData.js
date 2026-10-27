export function systemStatusPayload(result) {
  const raw = result?.raw && typeof result.raw === 'object' ? result.raw : null
  if (!raw || result?.name !== 'system_status') return null
  const gpuDevices = Array.isArray(raw.gpu?.devices) ? raw.gpu.devices : []
  const gpuText = gpuDevices.length
    ? gpuDevices.map((g) => `${g.name}: ${Math.round(g.vram_used_mb || 0)} / ${Math.round(g.vram_total_mb || 0)} MB VRAM`).join('\n')
    : 'No NVIDIA GPU telemetry available'
  return {
    kind: 'cards',
    title: 'System status',
    cards: [
      { title: 'CPU', value: `${Number(raw.cpu_percent || 0).toFixed(1)}%`, body: `${raw.cpu_count || '—'} logical processors` },
      { title: 'RAM', value: `${Number(raw.ram_percent || 0).toFixed(1)}%`, body: `${raw.ram_used_gb || 0} GB used of ${raw.ram_total_gb || 0} GB` },
      { title: 'Disk', value: `${Number(raw.disk_used_percent || 0).toFixed(1)}%`, body: `${raw.disk_free_gb || 0} GB free of ${raw.disk_total_gb || 0} GB on ${raw.disk_root || ''}` },
      { title: 'GPU', value: gpuDevices.length ? `${Number(gpuDevices[0].utilization_percent || 0).toFixed(0)}%` : '—', body: gpuText },
    ],
  }
}
