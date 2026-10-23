import { iconSvg } from "./tools.js";

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function safeUrl(value) {
  const decoded = String(value || "").replaceAll("&amp;", "&").trim();
  return /^https?:\/\//i.test(decoded) ? decoded : "";
}

function inlineMarkdown(escaped) {
  const codeSpans = [];
  let text = escaped.replace(/`([^`\n]+)`/g, (_match, code) => {
    codeSpans.push(code);
    return `\u0001${codeSpans.length - 1}\u0002`;
  });
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label, url) => {
    const href = safeUrl(url);
    return href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noreferrer">${label}</a>` : label;
  });
  text = text
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>")
    .replace(/~~([^~]+)~~/g, "<del>$1</del>");
  text = text.replace(/\u0001(\d+)\u0002/g, (_match, index) => `<code>${codeSpans[Number(index)]}</code>`);
  return text;
}

function renderTextBlock(block) {
  const lines = String(block || "").split(/\r?\n/);
  const html = [];
  let paragraph = [];
  let listType = "";
  let quote = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${inlineMarkdown(escapeHtml(paragraph.join("\n"))).replaceAll("\n", "<br>")}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = "";
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      html.push(`<blockquote>${inlineMarkdown(escapeHtml(quote.join("\n"))).replaceAll("\n", "<br>")}</blockquote>`);
      quote = [];
    }
  };
  const flushAll = () => { flushParagraph(); flushList(); flushQuote(); };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (!trimmed) { flushAll(); continue; }

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushAll();
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }
    if (/^---+$/.test(trimmed)) { flushAll(); html.push("<hr>"); continue; }
    if (trimmed.startsWith(">")) {
      flushParagraph(); flushList();
      quote.push(trimmed.replace(/^>\s?/, ""));
      continue;
    }

    const unordered = line.match(/^\s*[-*•]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph(); flushQuote();
      const nextType = unordered ? "ul" : "ol";
      if (listType !== nextType) {
        flushList();
        listType = nextType;
        html.push(`<${listType}>`);
      }
      html.push(`<li>${inlineMarkdown(escapeHtml((unordered || ordered)[1]))}</li>`);
      continue;
    }

    if (trimmed.includes("|") && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])) {
      flushAll();
      const split = (value) => value.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      const headers = split(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(split(lines[i]));
        i += 1;
      }
      i -= 1;
      html.push("<table><thead><tr>" + headers.map((h) => `<th>${inlineMarkdown(escapeHtml(h))}</th>`).join("") + "</tr></thead><tbody>");
      rows.forEach((row) => {
        html.push("<tr>" + headers.map((_h, index) => `<td>${inlineMarkdown(escapeHtml(row[index] || ""))}</td>`).join("") + "</tr>");
      });
      html.push("</tbody></table>");
      continue;
    }

    flushList(); flushQuote();
    paragraph.push(line);
  }
  flushAll();
  return html.join("");
}

export function renderMarkdown(text) {
  const source = String(text || "");
  const pieces = [];
  const fence = /```([^\n`]*)\n?([\s\S]*?)```/g;
  let last = 0;
  let match;
  while ((match = fence.exec(source)) !== null) {
    if (match.index > last) pieces.push(renderTextBlock(source.slice(last, match.index)));
    const language = (match[1] || "code").trim() || "code";
    pieces.push(
      `<div class="code-block"><div class="code-head"><span>${escapeHtml(language)}</span>` +
      `<button type="button" class="icon-button small copy-code" title="Copy code" aria-label="Copy code">` +
      `<svg aria-hidden="true"><use href="#icon-copy"></use></svg></button></div>` +
      `<pre><code>${escapeHtml(match[2] || "")}</code></pre></div>`,
    );
    last = fence.lastIndex;
  }
  const tail = source.slice(last);
  const openFence = tail.match(/```([^\n`]*)\n?([\s\S]*)$/);
  if (openFence && tail.trimStart().startsWith("```")) {
    const language = (openFence[1] || "code").trim() || "code";
    pieces.push(
      `<div class="code-block"><div class="code-head"><span>${escapeHtml(language)}</span></div>` +
      `<pre><code>${escapeHtml(openFence[2] || "")}</code></pre></div>`,
    );
  } else if (tail) {
    pieces.push(renderTextBlock(tail));
  }
  return pieces.join("") || "<p></p>";
}

export function extractThink(text) {
  let content = String(text || "");
  const reasoning = [];
  content = content.replace(/<think>([\s\S]*?)<\/think>/gi, (_match, value) => {
    reasoning.push(value.trim());
    return "";
  });
  const open = content.match(/<think>([\s\S]*)$/i);
  if (open) {
    reasoning.push(open[1].trim());
    content = content.replace(/<think>[\s\S]*$/i, "");
  }
  return { content: content.trim(), reasoning: reasoning.filter(Boolean).join("\n\n") };
}

function actionButton(action, icon, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button";
  button.dataset.action = action;
  button.title = label;
  button.setAttribute("aria-label", label);
  button.append(iconSvg(icon));
  return button;
}

function ensureReasoning(article) {
  let block = article.querySelector(".reasoning");
  if (block) return block;
  block = document.createElement("div");
  block.className = "reasoning";
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "reasoning-chip";
  chip.setAttribute("aria-expanded", "false");
  const label = document.createElement("span");
  label.textContent = "Reasoning";
  const dots = document.createElement("span");
  dots.className = "dot-loader";
  dots.innerHTML = "<i></i><i></i><i></i>";
  chip.append(label, dots, iconSvg("icon-chevron-right"));
  const body = document.createElement("div");
  body.className = "reasoning-body";
  chip.addEventListener("click", () => {
    const open = !block.classList.contains("open");
    block.classList.toggle("open", open);
    chip.setAttribute("aria-expanded", String(open));
  });
  block.append(chip, body);
  article.insertBefore(block, article.querySelector(".bubble"));
  block._labelEl = label;
  block._dotsEl = dots;
  block._bodyEl = body;
  return block;
}

export function createMessageElement(role = "assistant") {
  const row = document.createElement("div");
  row.className = `message-row ${role}`;
  const article = document.createElement("article");
  article.className = `message ${role}`;
  article._content = "";
  article._reasoning = "";
  article._streaming = false;

  const meta = document.createElement("div");
  meta.className = "message-meta";
  const author = document.createElement("span");
  author.textContent = role === "user" ? "You" : "VISION";
  const incomplete = document.createElement("span");
  incomplete.className = "incomplete-mark";
  incomplete.textContent = "Incomplete";
  incomplete.hidden = true;
  meta.append(author, incomplete);

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const markdown = document.createElement("div");
  markdown.className = "markdown";
  bubble.append(markdown);

  const actions = document.createElement("div");
  actions.className = "message-actions";
  actions.append(actionButton("copy-message", "icon-copy", "Copy message"));
  if (role === "assistant") {
    actions.append(actionButton("regenerate-message", "icon-refresh", "Regenerate"));
    actions.append(actionButton("feedback-message", "icon-thumb-down", "Mark as not helpful"));
    actions.append(actionButton("correct-message", "icon-pencil", "Correct this"));
  }

  article.append(meta, bubble, actions);
  row.append(article);
  article._markdownEl = markdown;
  article._incompleteEl = incomplete;
  return row;
}

export function updateMessageContent(row, { content = null, reasoning = null, streaming = false, incomplete = false } = {}) {
  const article = row.querySelector(".message");
  if (!article) return;
  if (content !== null) article._content = String(content);
  if (reasoning !== null) article._reasoning = String(reasoning);
  article._streaming = Boolean(streaming);

  const extracted = extractThink(article._content || "");
  const visibleContent = extracted.content;
  const combinedReasoning = [article._reasoning, extracted.reasoning].filter(Boolean).join("\n\n");

  if (combinedReasoning) {
    const block = ensureReasoning(article);
    block._bodyEl.textContent = combinedReasoning;
    block._labelEl.textContent = streaming && !visibleContent ? "Reasoning…" : "Reasoning";
    block._dotsEl.style.display = streaming ? "inline-flex" : "none";
  }

  article._markdownEl.innerHTML = renderMarkdown(visibleContent);
  if (streaming) {
    const cursor = document.createElement("span");
    cursor.className = "streaming-cursor";
    cursor.setAttribute("aria-hidden", "true");
    article._markdownEl.append(cursor);
  }
  article._incompleteEl.hidden = !incomplete;
}

export function appendMessageContent(row, text) {
  const article = row.querySelector(".message");
  updateMessageContent(row, { content: (article._content || "") + text, streaming: true });
}

export function appendMessageReasoning(row, text) {
  const article = row.querySelector(".message");
  updateMessageContent(row, { reasoning: (article._reasoning || "") + text, streaming: true });
}
