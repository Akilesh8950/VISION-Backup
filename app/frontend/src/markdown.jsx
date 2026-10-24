import React, { useState } from 'react'
import { Icon } from './icons.jsx'

function renderInline(text, keyPrefix, handlers) {
  const nodes = []
  let rest = String(text || '')
  let i = 0
  const pushText = (value) => { if (value) nodes.push(<React.Fragment key={`${keyPrefix}-${i++}`}>{value}</React.Fragment>) }
  while (rest.length) {
    let match
    if ((match = rest.match(/^`([^`\n]+)`/))) {
      nodes.push(<code key={`${keyPrefix}-${i++}`}>{match[1]}</code>)
    } else if (handlers.onSource && (match = rest.match(/^\[((?:S|L)\d+)\]/))) {
      const id = match[1]
      nodes.push(<button key={`${keyPrefix}-${i++}`} type="button" className="citation" title={`Open source ${id}`} onClick={() => handlers.onSource(id)}>{id}</button>)
    } else if ((match = rest.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/))) {
      nodes.push(<a key={`${keyPrefix}-${i++}`} href={match[2]} target="_blank" rel="noreferrer" onClick={(event) => { if (handlers.onOpenUrl?.(match[2])) event.preventDefault() }}>{match[1]}</a>)
    } else if ((match = rest.match(/^\*\*([^*]+)\*\*/) || rest.match(/^__([^_]+)__/))) {
      nodes.push(<strong key={`${keyPrefix}-${i++}`}>{renderInline(match[1], `${keyPrefix}-b${i}`, handlers)}</strong>)
    } else if ((match = rest.match(/^~~([^~]+)~~/))) {
      nodes.push(<del key={`${keyPrefix}-${i++}`}>{renderInline(match[1], `${keyPrefix}-d${i}`, handlers)}</del>)
    } else if ((match = rest.match(/^\*([^*\n]+)\*/) || rest.match(/^_([^_\n]+)_/))) {
      nodes.push(<em key={`${keyPrefix}-${i++}`}>{renderInline(match[1], `${keyPrefix}-e${i}`, handlers)}</em>)
    } else {
      const next = rest.slice(1).search(/[`[*_~]/)
      const take = next < 0 ? rest.length : next + 1
      pushText(rest.slice(0, take))
      rest = rest.slice(take)
      continue
    }
    rest = rest.slice(match[0].length)
  }
  return nodes
}

function Inline({ text, id, handlers }) {
  return <>{renderInline(text, id, handlers)}</>
}

export function CodeBlock({ language = 'code', code = '', onCopy }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await onCopy?.(code)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }
  return (
    <div className="code-block">
      <div className="code-head">
        <span>{language || 'code'}</span>
        <button type="button" className="code-copy" onClick={copy} aria-label="Copy code">
          <Icon name={copied ? 'check' : 'copy'} size={14} /><span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  )
}

function TextBlocks({ text, id, handlers }) {
  const lines = String(text || '').split(/\r?\n/)
  const blocks = []
  let paragraph = []
  let quote = []
  let list = null
  const flushParagraph = () => {
    if (!paragraph.length) return
    blocks.push(<p key={`${id}-p${blocks.length}`}><Inline text={paragraph.join('\n')} id={`${id}-p${blocks.length}`} handlers={handlers} /></p>)
    paragraph = []
  }
  const flushQuote = () => {
    if (!quote.length) return
    blocks.push(<blockquote key={`${id}-q${blocks.length}`}><Inline text={quote.join('\n')} id={`${id}-q${blocks.length}`} handlers={handlers} /></blockquote>)
    quote = []
  }
  const flushList = () => {
    if (!list) return
    const Tag = list.type
    blocks.push(<Tag key={`${id}-l${blocks.length}`}>{list.items.map((item, index) => <li key={index}><Inline text={item} id={`${id}-li${blocks.length}-${index}`} handlers={handlers} /></li>)}</Tag>)
    list = null
  }
  const flushAll = () => { flushParagraph(); flushQuote(); flushList() }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    const trimmed = line.trim()
    if (!trimmed) { flushAll(); continue }
    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/)
    if (heading) {
      flushAll()
      const Tag = `h${heading[1].length}`
      blocks.push(<Tag key={`${id}-h${blocks.length}`}><Inline text={heading[2]} id={`${id}-h${blocks.length}`} handlers={handlers} /></Tag>)
      continue
    }
    if (/^---+$/.test(trimmed)) { flushAll(); blocks.push(<hr key={`${id}-hr${blocks.length}`} />); continue }
    if (trimmed.startsWith('>')) { flushParagraph(); flushList(); quote.push(trimmed.replace(/^>\s?/, '')); continue }
    const unordered = line.match(/^\s*[-*•]\s+(.+)$/)
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/)
    if (unordered || ordered) {
      flushParagraph(); flushQuote()
      const type = unordered ? 'ul' : 'ol'
      if (!list || list.type !== type) { flushList(); list = { type, items: [] } }
      list.items.push((unordered || ordered)[1])
      continue
    }
    if (trimmed.includes('|') && index + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1])) {
      flushAll()
      const split = (value) => value.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim())
      const headers = split(line)
      const rows = []
      index += 2
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) { rows.push(split(lines[index])); index += 1 }
      index -= 1
      blocks.push(
        <div className="md-table-wrap" key={`${id}-t${blocks.length}`}><table>
          <thead><tr>{headers.map((h, hIndex) => <th key={hIndex}><Inline text={h} id={`${id}-th${blocks.length}-${hIndex}`} handlers={handlers} /></th>)}</tr></thead>
          <tbody>{rows.map((row, rIndex) => <tr key={rIndex}>{headers.map((_, cIndex) => <td key={cIndex}><Inline text={row[cIndex] || ''} id={`${id}-td${blocks.length}-${rIndex}-${cIndex}`} handlers={handlers} /></td>)}</tr>)}</tbody>
        </table></div>,
      )
      continue
    }
    flushQuote(); flushList()
    paragraph.push(line)
  }
  flushAll()
  return blocks
}

export function Markdown({ text = '', onCopy, onSource, onOpenUrl }) {
  const handlers = { onCopy, onSource, onOpenUrl }
  const source = String(text || '')
  const out = []
  const fence = /```([^\n`]*)\n?([\s\S]*?)```/g
  let last = 0
  let match
  let part = 0
  while ((match = fence.exec(source)) !== null) {
    if (match.index > last) out.push(<TextBlocks key={`t${part++}`} text={source.slice(last, match.index)} id={`t${part}`} handlers={handlers} />)
    out.push(<CodeBlock key={`c${part++}`} language={(match[1] || 'code').trim()} code={match[2] || ''} onCopy={onCopy} />)
    last = fence.lastIndex
  }
  const tail = source.slice(last)
  const open = tail.match(/```([^\n`]*)\n?([\s\S]*)$/)
  if (open && tail.trimStart().startsWith('```')) {
    out.push(<CodeBlock key={`c${part++}`} language={(open[1] || 'code').trim()} code={open[2] || ''} onCopy={onCopy} />)
  } else if (tail) {
    out.push(<TextBlocks key={`t${part++}`} text={tail} id={`t${part}`} handlers={handlers} />)
  }
  return <div className="markdown">{out.length ? out : <p />}</div>
}

export function extractThink(text) {
  let content = String(text || '')
  const reasoning = []
  content = content.replace(/<think>([\s\S]*?)<\/think>/gi, (_m, value) => { reasoning.push(value.trim()); return '' })
  const open = content.match(/<think>([\s\S]*)$/i)
  if (open) { reasoning.push(open[1].trim()); content = content.replace(/<think>[\s\S]*$/i, '') }
  return { content: content.trim(), reasoning: reasoning.filter(Boolean).join('\n\n') }
}
