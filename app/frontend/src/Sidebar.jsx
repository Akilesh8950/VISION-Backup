import React, { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Icon, LogoMark } from './icons.jsx'

const ROW_HEIGHTS = {
  section: 32,
  project: 40,
  chat: 42,
  date: 26,
  empty: 42,
  railChat: 42,
  railProject: 42,
  spacer: 10,
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

function dateGroup(value) {
  const ms = timestampMs(value)
  if (!ms) return 'Earlier'
  const date = new Date(ms)
  const now = new Date()
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  if (day >= start) return 'Today'
  if (day >= start - 86400000) return 'Yesterday'
  if (day >= start - 6 * 86400000) return 'Previous 7 days'
  return 'Earlier'
}

function projectLabel(project) {
  return String(project?.name || project?.title || project?.id || 'Project')
}

const ChatRow = memo(function ChatRow({
  row,
  active,
  projectName,
  showProject,
  editing,
  draft,
  onSelect,
  onStartEdit,
  onDraftChange,
  onSaveEdit,
  onCancelEdit,
  onOpenMenu,
}) {
  const title = row.title || 'New chat'
  return (
    <div className={`chat-item ${active ? 'active' : ''}`}>
      {editing ? (
        <input
          className="chat-title-input"
          value={draft}
          aria-label="Chat title"
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
        <button
          type="button"
          className="chat-main"
          title={`${title} — double-click to rename`}
          onClick={() => onSelect(row.id)}
          onDoubleClick={(event) => { event.preventDefault(); onStartEdit(row) }}
        >
          <Icon name={row.pinned ? 'pin' : 'message'} size={15} />
          <span className="chat-title">{title}</span>
          {showProject && projectName ? <span className="chat-project" title={projectName}><Icon name="folder" size={11} /></span> : null}
          <span className="chat-date">{formatDate(row.updated_at || row.created_at)}</span>
        </button>
      )}
      {!editing ? (
        <button
          type="button"
          className="icon-btn small chat-menu-button"
          title={`More actions for ${title}`}
          aria-label={`More actions for ${title}`}
          aria-haspopup="menu"
          onClick={(event) => onOpenMenu(event, row)}
        >
          <Icon name="dots" size={16} />
        </button>
      ) : null}
    </div>
  )
})

export const Sidebar = memo(function Sidebar({
  collapsed,
  conversations = [],
  projects = [],
  currentId = '',
  search = '',
  projectFilter = 'all',
  collapsedProjects = {},
  libraryCount = 0,
  theme = 'dark',
  onToggleCollapsed,
  onNewChat,
  onSearchChange,
  onProjectFilterChange,
  onCreateProject,
  onToggleProjectGroup,
  onSelectConversation,
  onRenameConversation,
  onTogglePinned,
  onAssignProject,
  onDeleteConversation,
  onOpenLibrary,
  onOpenServer,
  onOpenActivity,
  onOpenSettings,
  onToggleTheme,
}) {
  const viewportRef = useRef(null)
  const searchRef = useRef(null)
  const scrollFrameRef = useRef(0)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(420)
  const [menu, setMenu] = useState(null)
  const [editingId, setEditingId] = useState('')
  const [editingDraft, setEditingDraft] = useState('')
  const [focusSearch, setFocusSearch] = useState(false)

  const projectById = useMemo(() => {
    const map = new Map()
    for (const project of projects) map.set(String(project.id), project)
    return map
  }, [projects])

  const rows = useMemo(() => {
    const query = search.trim().toLowerCase()
    const visible = conversations
      .filter((item) => !item.archived)
      .filter((item) => {
        if (!query) return true
        const title = String(item.title || 'New chat').toLowerCase()
        const projectName = projectLabel(projectById.get(String(item.project_id || ''))).toLowerCase()
        return Boolean(item._search_match) || title.includes(query) || projectName.includes(query)
      })
      .sort((a, b) => (
        Number(Boolean(b.pinned)) - Number(Boolean(a.pinned))
        || timestampMs(b.updated_at || b.created_at) - timestampMs(a.updated_at || a.created_at)
      ))

    if (collapsed) {
      const railRows = visible.map((row) => ({ type: 'railChat', key: `rail-${row.id}`, row }))
      if (projects.length) {
        railRows.push({ type: 'spacer', key: 'rail-project-spacer' })
        for (const project of projects) {
          railRows.push({ type: 'railProject', key: `rail-project-${project.id}`, project })
        }
      }
      return railRows.length ? railRows : [{ type: 'empty', key: 'rail-empty', label: 'No chats' }]
    }

    const filtered = visible.filter((item) => {
      if (projectFilter === 'all') return true
      if (projectFilter === 'none') return !item.project_id
      return String(item.project_id || '') === String(projectFilter)
    })
    const pinned = filtered.filter((item) => Boolean(item.pinned))
    const unpinned = filtered.filter((item) => !item.pinned)
    const output = []

    if (pinned.length) {
      output.push({ type: 'section', key: 'section-pinned', label: 'Pinned', count: pinned.length })
      for (const row of pinned) output.push({ type: 'chat', key: `pinned-${row.id}`, row, section: 'pinned' })
    }

    const projectRows = projectFilter === 'none' ? [] : projects.filter((project) => (
      projectFilter === 'all' || String(project.id) === String(projectFilter)
    ))
    if (projectRows.length) {
      output.push({ type: 'section', key: 'section-projects', label: 'Projects', count: projectRows.length })
      for (const project of projectRows) {
        const chats = unpinned.filter((item) => String(item.project_id || '') === String(project.id))
        const total = filtered.filter((item) => String(item.project_id || '') === String(project.id)).length
        const isCollapsed = Boolean(collapsedProjects[String(project.id)])
        output.push({
          type: 'project', key: `project-${project.id}`, project, count: total, collapsed: isCollapsed,
          activeFilter: String(projectFilter) === String(project.id),
        })
        if (!isCollapsed) {
          if (!chats.length) {
            output.push({
              type: 'empty', key: `project-empty-${project.id}`,
              label: total ? 'Pinned chats appear above' : 'No chats yet', projectId: String(project.id),
            })
          }
          for (const row of chats) output.push({ type: 'chat', key: `project-chat-${row.id}`, row, section: 'project' })
        }
      }
    }

    if (projectFilter === 'all' || projectFilter === 'none') {
      const history = projectFilter === 'all' ? unpinned.filter((item) => !item.project_id) : unpinned
      output.push({ type: 'section', key: 'section-history', label: 'History', count: history.length })
      if (!history.length) {
        output.push({ type: 'empty', key: 'history-empty', label: search ? 'No chats match your search' : 'No unassigned chats' })
      } else {
        let lastGroup = ''
        for (const row of history) {
          const group = dateGroup(row.updated_at || row.created_at)
          if (group !== lastGroup) {
            output.push({ type: 'date', key: `date-${group}-${row.id}`, label: group })
            lastGroup = group
          }
          output.push({ type: 'chat', key: `history-${row.id}`, row, section: 'history' })
        }
      }
    }

    if (!output.length || (!filtered.length && !projectRows.length)) {
      return [{ type: 'empty', key: 'sidebar-empty', label: search ? 'No chats match your search' : 'No chats yet' }]
    }
    return output
  }, [collapsed, collapsedProjects, conversations, projectById, projectFilter, projects, search])

  const offsets = useMemo(() => {
    const values = []
    let cursor = 0
    for (const row of rows) {
      values.push(cursor)
      cursor += ROW_HEIGHTS[row.type] || ROW_HEIGHTS.chat
    }
    return { values, total: cursor }
  }, [rows])

  const visibleRange = useMemo(() => {
    if (!rows.length) return { start: 0, end: 0 }
    const overscan = 8
    let start = rows.findIndex((row, index) => offsets.values[index] + (ROW_HEIGHTS[row.type] || 42) >= scrollTop)
    if (start < 0) start = rows.length - 1
    let end = start
    const limit = scrollTop + viewportHeight
    while (end < rows.length && offsets.values[end] <= limit) end += 1
    return { start: Math.max(0, start - overscan), end: Math.min(rows.length, end + overscan) }
  }, [offsets, rows, scrollTop, viewportHeight])

  useEffect(() => {
    const node = viewportRef.current
    if (!node) return undefined
    const update = () => setViewportHeight(node.clientHeight || 420)
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [collapsed])

  useEffect(() => {
    if (!collapsed && focusSearch) {
      setFocusSearch(false)
      window.setTimeout(() => searchRef.current?.focus(), 200)
    }
  }, [collapsed, focusSearch])

  useEffect(() => {
    const node = viewportRef.current
    if (!node || !currentId) return
    const index = rows.findIndex((row) => (row.type === 'chat' || row.type === 'railChat') && String(row.row.id) === String(currentId))
    if (index < 0) return
    const top = offsets.values[index]
    const height = ROW_HEIGHTS[rows[index].type] || 42
    if (top < node.scrollTop || top + height > node.scrollTop + node.clientHeight) {
      node.scrollTop = Math.max(0, top - 12)
      setScrollTop(node.scrollTop)
    }
  }, [currentId, offsets, rows])

  useEffect(() => {
    if (!menu) return
    if (!conversations.some((row) => String(row.id) === String(menu.row.id))) setMenu(null)
  }, [conversations, menu])

  useEffect(() => {
    if (!menu) return undefined
    const onPointerDown = (event) => {
      if (!event.target.closest('.chat-menu') && !event.target.closest('.chat-menu-button')) setMenu(null)
    }
    const onKeyDown = (event) => { if (event.key === 'Escape') setMenu(null) }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [menu])

  const handleScroll = useCallback((event) => {
    setMenu(null)
    const next = event.currentTarget.scrollTop
    if (scrollFrameRef.current) return
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = 0
      setScrollTop(next)
    })
  }, [])

  const startEdit = useCallback((row) => {
    setMenu(null)
    setEditingId(String(row.id))
    setEditingDraft(row.title || 'New chat')
  }, [])

  const saveEdit = useCallback(() => {
    const id = editingId
    const value = editingDraft.trim()
    setEditingId('')
    setEditingDraft('')
    if (id && value) onRenameConversation?.(id, value)
  }, [editingDraft, editingId, onRenameConversation])

  const cancelEdit = useCallback(() => {
    setEditingId('')
    setEditingDraft('')
  }, [])

  const openMenu = useCallback((event, row) => {
    event.preventDefault()
    event.stopPropagation()
    const rect = event.currentTarget.getBoundingClientRect()
    const width = 238
    const estimatedHeight = 330
    setMenu({
      row,
      x: Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8)),
      y: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - estimatedHeight - 8)),
    })
  }, [])

  const renderRow = (virtualRow) => {
    if (virtualRow.type === 'section') {
      return (
        <div className="side-section-row" role="presentation">
          <span>{virtualRow.label}</span>
          {virtualRow.count != null ? <span className="side-count">{virtualRow.count}</span> : null}
        </div>
      )
    }
    if (virtualRow.type === 'date') return <div className="chat-group virtual-date">{virtualRow.label}</div>
    if (virtualRow.type === 'empty') return <div className="sidebar-empty-row">{virtualRow.label}</div>
    if (virtualRow.type === 'spacer') return <div className="rail-spacer" />
    if (virtualRow.type === 'railProject') {
      const project = virtualRow.project
      return (
        <button
          type="button"
          className={`rail-project ${String(projectFilter) === String(project.id) ? 'active' : ''}`}
          title={`${projectLabel(project)} — expand and filter`}
          aria-label={`${projectLabel(project)} — expand and filter`}
          onClick={() => { onProjectFilterChange?.(String(project.id)); onToggleCollapsed?.() }}
        >
          <Icon name="folder" size={17} />
        </button>
      )
    }
    if (virtualRow.type === 'railChat') {
      const row = virtualRow.row
      return (
        <button
          type="button"
          className={`rail-chat ${String(row.id) === String(currentId) ? 'active' : ''}`}
          title={row.title || 'New chat'}
          aria-label={row.title || 'New chat'}
          onClick={() => onSelectConversation?.(row.id)}
        >
          <Icon name={row.pinned ? 'pin' : 'message'} size={17} />
        </button>
      )
    }
    if (virtualRow.type === 'project') {
      const project = virtualRow.project
      return (
        <div className={`project-row ${virtualRow.activeFilter ? 'filtered' : ''}`}>
          <button
            type="button"
            className="project-main"
            aria-expanded={!virtualRow.collapsed}
            title={`${virtualRow.collapsed ? 'Expand' : 'Collapse'} ${projectLabel(project)}`}
            onClick={() => onToggleProjectGroup?.(String(project.id))}
          >
            <span className={`project-chev ${virtualRow.collapsed ? 'closed' : ''}`}><Icon name="chevD" size={13} /></span>
            <Icon name="folder" size={15} />
            <span className="project-name">{projectLabel(project)}</span>
            <span className="side-count">{virtualRow.count}</span>
          </button>
          <button
            type="button"
            className="icon-btn small project-filter-btn"
            title={virtualRow.activeFilter ? 'Show all projects' : `Filter to ${projectLabel(project)}`}
            aria-label={virtualRow.activeFilter ? 'Show all projects' : `Filter to ${projectLabel(project)}`}
            onClick={() => onProjectFilterChange?.(virtualRow.activeFilter ? 'all' : String(project.id))}
          >
            <Icon name={virtualRow.activeFilter ? 'check' : 'filter'} size={13} />
          </button>
          <button
            type="button"
            className="icon-btn small project-new-btn"
            title={`New chat in ${projectLabel(project)}`}
            aria-label={`New chat in ${projectLabel(project)}`}
            onClick={() => onNewChat?.(String(project.id))}
          >
            <Icon name="plus" size={13} />
          </button>
        </div>
      )
    }

    const row = virtualRow.row
    return (
      <ChatRow
        row={row}
        active={String(row.id) === String(currentId)}
        projectName={projectLabel(projectById.get(String(row.project_id || '')))}
        showProject={Boolean(row.project_id && virtualRow.section !== 'project')}
        editing={String(editingId) === String(row.id)}
        draft={editingDraft}
        onSelect={onSelectConversation}
        onStartEdit={startEdit}
        onDraftChange={setEditingDraft}
        onSaveEdit={saveEdit}
        onCancelEdit={cancelEdit}
        onOpenMenu={openMenu}
      />
    )
  }

  const menuRow = menu?.row
    ? conversations.find((row) => String(row.id) === String(menu.row.id)) || menu.row
    : null
  const visibleRows = rows.slice(visibleRange.start, visibleRange.end)

  return (
    <aside className="sidebar" aria-label="Conversations and projects">
      <div className="brand">
        <button
          type="button"
          className="logo-toggle"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
          onClick={onToggleCollapsed}
        >
          <LogoMark size={34} />
        </button>
        {!collapsed ? <div className="brand-text"><div className="word">VISION</div><div className="by">built by SPIRIT ORG</div></div> : null}
      </div>

      <button type="button" className="new-chat" title="New chat" aria-label="New chat" onClick={() => onNewChat?.()}>
        <Icon name="compose" size={17} />{!collapsed ? <span>New chat</span> : null}
      </button>

      {collapsed ? (
        <button
          type="button"
          className="rail-search"
          title="Search chats"
          aria-label="Search chats"
          onClick={() => { setFocusSearch(true); onToggleCollapsed?.() }}
        >
          <Icon name="search" size={17} />
        </button>
      ) : (
        <>
          <div className="search">
            <Icon name="search" size={15} />
            <input
              ref={searchRef}
              id="vision-chat-search"
              value={search}
              onChange={(event) => onSearchChange?.(event.target.value)}
              placeholder="Search chats"
              aria-label="Search chats"
            />
            {search ? (
              <button type="button" className="search-clear" aria-label="Clear search" onClick={() => onSearchChange?.('')}>
                <Icon name="x" size={12} />
              </button>
            ) : null}
          </div>
          <div className="project-filter-row">
            <label className="project-filter-select">
              <Icon name="filter" size={13} />
              <select value={projectFilter} aria-label="Filter chats by project" onChange={(event) => onProjectFilterChange?.(event.target.value)}>
                <option value="all">All projects</option>
                <option value="none">No project</option>
                {projects.map((project) => <option key={project.id} value={project.id}>{projectLabel(project)}</option>)}
              </select>
            </label>
            <button type="button" className="icon-btn small" title="Create project" aria-label="Create project" onClick={onCreateProject}>
              <Icon name="plus" size={14} />
            </button>
          </div>
        </>
      )}

      <nav className="chat-list virtual-list" ref={viewportRef} onScroll={handleScroll} aria-label="Chat history">
        <div className="virtual-spacer" style={{ height: offsets.total }}>
          {visibleRows.map((virtualRow, localIndex) => {
            const index = visibleRange.start + localIndex
            return (
              <div
                key={virtualRow.key}
                className="virtual-row"
                style={{ transform: `translateY(${offsets.values[index]}px)`, height: ROW_HEIGHTS[virtualRow.type] || ROW_HEIGHTS.chat }}
              >
                {renderRow(virtualRow)}
              </div>
            )
          })}
        </div>
      </nav>

      <div className="side-foot">
        <button type="button" className="nav-btn" title="Item Library" aria-label="Item Library" onClick={onOpenLibrary}>
          <Icon name="library" size={17} />{!collapsed ? <span>Item Library</span> : null}
          {!collapsed && libraryCount ? <span className="nav-badge">{libraryCount}</span> : null}
        </button>
        <button type="button" className="nav-btn" title="Server" aria-label="Server" onClick={onOpenServer}>
          <Icon name="server" size={17} />{!collapsed ? <span>Server</span> : null}
        </button>
        <button type="button" className="nav-btn" title="Activity & feedback" aria-label="Activity and feedback" onClick={onOpenActivity}>
          <Icon name="pulse" size={17} />{!collapsed ? <span>Activity & feedback</span> : null}
        </button>
        <button type="button" className="nav-btn settings-footer-btn" title="Settings" aria-label="Settings" onClick={() => onOpenSettings?.('general')}>
          <Icon name="sliders" size={17} />{!collapsed ? <span>Settings</span> : null}
        </button>
        <button type="button" className="nav-btn" title={theme === 'dark' ? 'Light theme' : 'Dark theme'} aria-label={theme === 'dark' ? 'Light theme' : 'Dark theme'} onClick={onToggleTheme}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={17} />{!collapsed ? <span>{theme === 'dark' ? 'Light theme' : 'Dark theme'}</span> : null}
        </button>
      </div>

      {menuRow ? (
        <div className="chat-menu" role="menu" style={{ left: menu.x, top: menu.y }} aria-label={`Actions for ${menuRow.title || 'New chat'}`}>
          <div className="chat-menu-title" title={menuRow.title || 'New chat'}>{menuRow.title || 'New chat'}</div>
          <button type="button" role="menuitem" onClick={() => startEdit(menuRow)}>
            <Icon name="pencil" size={14} /><span>Rename</span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => { setMenu(null); onTogglePinned?.(menuRow) }}
          >
            <Icon name="pin" size={14} /><span>{menuRow.pinned ? 'Unpin chat' : 'Pin chat'}</span>
          </button>
          <div className="chat-menu-label">Move to project</div>
          <div className="chat-menu-projects">
            <button
              type="button"
              role="menuitem"
              onClick={() => { setMenu(null); onAssignProject?.(menuRow, '') }}
            >
              <Icon name={!menuRow.project_id ? 'check' : 'message'} size={14} /><span>No project</span>
            </button>
            {projects.map((project) => (
              <button
                key={project.id}
                type="button"
                role="menuitem"
                onClick={() => { setMenu(null); onAssignProject?.(menuRow, String(project.id)) }}
              >
                <Icon name={String(menuRow.project_id || '') === String(project.id) ? 'check' : 'folder'} size={14} />
                <span>{projectLabel(project)}</span>
              </button>
            ))}
          </div>
          <div className="chat-menu-separator" />
          <button
            type="button"
            role="menuitem"
            className="danger"
            onClick={() => { setMenu(null); onDeleteConversation?.(menuRow) }}
          >
            <Icon name="trash" size={14} /><span>{menuRow.pinned ? 'Unpin & delete…' : 'Delete…'}</span>
          </button>
          {menuRow.pinned ? <p className="chat-menu-note">Pinned chats require an extra confirmation before deletion.</p> : null}
        </div>
      ) : null}
    </aside>
  )
})
