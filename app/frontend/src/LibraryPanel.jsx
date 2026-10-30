import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from './icons.jsx'

const ROW_HEIGHT = 82

function formatBytes(value) {
  const n = Number(value) || 0
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)))
  return `${(n / 1024 ** i).toFixed(i ? 2 : 0)} ${units[i]}`
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function iconForKind(kind) {
  switch (kind) {
    case 'image': return 'image'
    case 'audio': return 'music'
    case 'video': return 'film'
    case 'archive': return 'archive'
    case 'code': return 'code'
    case 'data': return 'database'
    case 'spreadsheet': return 'chart'
    case 'document': return 'book'
    default: return 'file'
  }
}

function extensionLabel(item) {
  return String(item.extension || '').replace(/^\./, '').toUpperCase() || String(item.kind || 'FILE').toUpperCase()
}

const LibraryRow = memo(function LibraryRow({
  item,
  attached,
  editing,
  draft,
  onDraftChange,
  onStartEdit,
  onSaveEdit,
  onCancelEdit,
  onAttach,
  onDelete,
}) {
  const used = Number(item.used_in_chats || 0)
  return (
    <article className="library-item">
      <div className={`library-thumb kind-${item.kind || 'file'}`} aria-hidden="true">
        {item.thumbnail ? <img src={item.thumbnail} alt="" decoding="async" /> : <><Icon name={iconForKind(item.kind)} size={21} /><span>{extensionLabel(item)}</span></>}
      </div>
      <div className="library-item-main">
        {editing ? (
          <input
            className="library-name-input"
            value={draft}
            aria-label="Library item name"
            autoFocus
            onFocus={(event) => event.target.select()}
            onChange={(event) => onDraftChange(event.target.value)}
            onBlur={onSaveEdit}
            onKeyDown={(event) => {
              if (event.key === 'Enter') { event.preventDefault(); onSaveEdit() }
              if (event.key === 'Escape') { event.preventDefault(); onCancelEdit() }
            }}
          />
        ) : (
          <button type="button" className="library-name" title={`${item.name} — double-click to rename`} onDoubleClick={() => onStartEdit(item)}>
            {item.name || 'Untitled file'}
          </button>
        )}
        <div className="library-meta">
          <span>{extensionLabel(item)}</span><span aria-hidden="true">·</span><span>{formatBytes(item.size)}</span>
          {formatDate(item.updated_at || item.created_at) ? <><span aria-hidden="true">·</span><span>{formatDate(item.updated_at || item.created_at)}</span></> : null}
        </div>
        <div className="library-used">Used in {used} {used === 1 ? 'chat' : 'chats'}</div>
      </div>
      <div className="library-actions">
        <button
          type="button"
          className={`icon-btn small ${attached ? 'attached' : ''}`}
          title={!item.attachable ? 'Only UTF-8 text files up to 256 KB can be attached' : attached ? 'Attached to this message' : 'Attach to message'}
          aria-label={!item.attachable ? `${item.name} cannot be attached` : attached ? `${item.name} attached` : `Attach ${item.name} to message`}
          disabled={!item.attachable}
          onClick={() => onAttach(item)}
        >
          <Icon name={attached ? 'check' : 'plus'} size={14} />
        </button>
        <button type="button" className="icon-btn small" title="Rename" aria-label={`Rename ${item.name}`} onClick={() => onStartEdit(item)}>
          <Icon name="pencil" size={14} />
        </button>
        <button type="button" className="icon-btn small danger" title="Delete from library" aria-label={`Delete ${item.name} from library`} onClick={() => onDelete(item)}>
          <Icon name="trash" size={14} />
        </button>
      </div>
    </article>
  )
})

function LibraryPanelView({
  open = true,
  embedded = false,
  items = [],
  attachments = [],
  loading = false,
  onClose,
  onUpload,
  onAttach,
  onRename,
  onDelete,
  onRefresh,
}) {
  const viewportRef = useRef(null)
  const scrollFrameRef = useRef(0)
  const [query, setQuery] = useState('')
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(480)
  const [editingId, setEditingId] = useState('')
  const [draft, setDraft] = useState('')

  const attachedIds = useMemo(() => new Set(
    attachments.map((item) => String(item.library_item_id || item.libraryId || '')).filter(Boolean),
  ), [attachments])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((item) => `${item.name || ''} ${item.extension || ''} ${item.kind || ''}`.toLowerCase().includes(q))
  }, [items, query])

  const range = useMemo(() => {
    const overscan = 6
    const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - overscan)
    const end = Math.min(filtered.length, Math.ceil((scrollTop + viewportHeight) / ROW_HEIGHT) + overscan)
    return { start, end }
  }, [filtered.length, scrollTop, viewportHeight])

  useEffect(() => {
    const node = viewportRef.current
    if (!node) return undefined
    const update = () => setViewportHeight(node.clientHeight || 480)
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [open, embedded])

  const handleScroll = useCallback((event) => {
    const next = event.currentTarget.scrollTop
    if (scrollFrameRef.current) return
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = 0
      setScrollTop(next)
    })
  }, [])

  const startEdit = useCallback((item) => {
    setEditingId(String(item.id))
    setDraft(item.name || '')
  }, [])

  const saveEdit = useCallback(() => {
    const id = editingId
    const value = draft.trim()
    setEditingId('')
    setDraft('')
    if (id && value) onRename?.(id, value)
  }, [draft, editingId, onRename])

  const cancelEdit = useCallback(() => {
    setEditingId('')
    setDraft('')
  }, [])

  if (!open && !embedded) return null

  const content = (
    <>
      <div className="library-head">
        <div>
          <h2>Item Library</h2>
          <p>{items.length} uploaded {items.length === 1 ? 'file' : 'files'} saved on this device</p>
        </div>
        <div className="library-head-actions">
          <button type="button" className="icon-btn" title="Refresh library" aria-label="Refresh library" onClick={onRefresh}><Icon name="refresh" size={16} /></button>
          {!embedded ? <button type="button" className="icon-btn" title="Close library" aria-label="Close library" onClick={onClose}><Icon name="x" size={16} /></button> : null}
        </div>
      </div>
      <div className="library-toolbar">
        <div className="library-search">
          <Icon name="search" size={14} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search files" aria-label="Search library files" />
          {query ? <button type="button" aria-label="Clear library search" onClick={() => setQuery('')}><Icon name="x" size={12} /></button> : null}
        </div>
        <button type="button" className="btn small primary" onClick={onUpload}><Icon name="plus" size={14} />Upload</button>
      </div>
      {loading ? (
        <div className="library-empty"><span className="spinner" /><span>Loading library…</span></div>
      ) : filtered.length === 0 ? (
        <div className="library-empty">
          <span className="library-empty-icon"><Icon name="library" size={22} /></span>
          <strong>{query ? 'No matching files' : 'Your library is empty'}</strong>
          <span>{query ? 'Try a different search.' : 'Upload a file once, then attach it to any message.'}</span>
        </div>
      ) : (
        <div className="library-list virtual-list" ref={viewportRef} onScroll={handleScroll}>
          <div className="virtual-spacer" style={{ height: filtered.length * ROW_HEIGHT }}>
            {filtered.slice(range.start, range.end).map((item, localIndex) => {
              const index = range.start + localIndex
              return (
                <div
                  key={item.id}
                  className="virtual-row library-row"
                  style={{ transform: `translateY(${index * ROW_HEIGHT}px)`, height: ROW_HEIGHT }}
                >
                  <LibraryRow
                    item={item}
                    attached={attachedIds.has(String(item.id))}
                    editing={String(editingId) === String(item.id)}
                    draft={draft}
                    onDraftChange={setDraft}
                    onStartEdit={startEdit}
                    onSaveEdit={saveEdit}
                    onCancelEdit={cancelEdit}
                    onAttach={onAttach}
                    onDelete={onDelete}
                  />
                </div>
              )
            })}
          </div>
        </div>
      )}
      <p className="library-footnote">Text files up to 256 KB can be attached. Other formats stay safely stored for reference.</p>
    </>
  )

  if (embedded) return <section className="library-panel embedded" aria-label="Item Library">{content}</section>
  return <aside className={`library-panel ${open ? 'open' : ''}`} aria-label="Item Library">{content}</aside>
}

export default memo(LibraryPanelView)
