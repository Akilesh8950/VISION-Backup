import { call, callOptional, isNative, on, waitForNative } from "./ipc-bridge.js";
import { animateIn, animateSheet, animateToast, bindPressFeedback } from "./motion.js";
import { createMessageElement, updateMessageContent, appendMessageContent, appendMessageReasoning } from "./stream.js";
import { createToolCard, finishToolCard, parseArguments } from "./tools.js";
import { renderGenUI } from "./widgets.js";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const el = {
  workspace: $("#workspace"),
  transcript: $("#transcript"),
  transcriptInner: $("#transcriptInner"),
  emptyState: $("#emptyState"),
  jumpLatest: $("#jumpLatest"),
  composerForm: $("#composerForm"),
  composerInput: $("#composerInput"),
  sendButton: $("#sendButton"),
  attachButton: $("#attachButton"),
  contextChips: $("#contextChips"),
  tokenCount: $("#tokenCount"),
  modelChip: $("#modelChip"),
  conversationList: $("#conversationList"),
  conversationSearch: $("#conversationSearch"),
  projectSelect: $("#projectSelect"),
  windowConversationTitle: $("#windowConversationTitle"),
  serverDot: $("#serverDot"),
  serverPillText: $("#serverPillText"),
  banner: $("#banner"),
  bannerText: $("#bannerText"),
  inspector: $("#inspector"),
  sourcesList: $("#sourcesList"),
  toastStack: $("#toastStack"),
};

const state = {
  native: false,
  config: {},
  credentials: {},
  paths: {},
  app: {},
  conversations: [],
  projects: [],
  skills: [],
  extensions: { servers: [], connectors: [], dynamic_tools: [], messages: [] },
  currentConversationId: "",
  currentConversation: null,
  attachments: [],
  sources: [],
  server: {},
  streaming: false,
  stickToBottom: true,
  currentAssistantRow: null,
  currentToolStack: null,
  toolCards: new Map(),
  lastEndpoint: null,
};

let promptResolver = null;
const confirmationQueue = [];
let activeConfirmation = null;

function esc(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function icon(name) {
  return `<svg aria-hidden="true"><use href="#${name}"></use></svg>`;
}

async function api(method, ...args) {
  if (!state.native) return null;
  try {
    const result = await call(method, ...args);
    if (result && result.ok === false && result.message && !result.cancelled) {
      showToast(result.message, "error");
    }
    return result;
  } catch (error) {
    showToast(error.message || `Could not call ${method}`, "error");
    return null;
  }
}

function showToast(message, kind = "info") {
  if (!message) return;
  while (el.toastStack.children.length >= 3) el.toastStack.firstElementChild.remove();
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.innerHTML = `${icon(kind === "error" ? "icon-x" : kind === "success" ? "icon-check" : "icon-info")}<span></span>`;
  toast.querySelector("span").textContent = String(message);
  el.toastStack.append(toast);
  animateToast(toast);
  window.setTimeout(() => toast.remove(), 3000);
}

function showBanner(message) {
  if (!message) return;
  el.bannerText.textContent = String(message);
  el.banner.hidden = false;
}

function hideBanner() {
  el.banner.hidden = true;
  el.bannerText.textContent = "";
}

function setTheme(theme, persistLocal = true) {
  const clean = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = clean;
  state.config.theme = clean;
  if (persistLocal) localStorage.setItem("vision.theme", clean);
  const toggle = $("#themeToggle");
  if (toggle) toggle.innerHTML = `${icon(clean === "dark" ? "icon-sun" : "icon-moon")}<span class="hide-collapsed">Theme</span>`;
  const select = $("#setTheme");
  if (select) select.value = clean;
}

function setSidebarCollapsed(collapsed) {
  el.workspace.classList.toggle("sidebar-collapsed", Boolean(collapsed));
  localStorage.setItem("vision.sidebarCollapsed", collapsed ? "1" : "0");
  $("#sidebarToggle").title = collapsed ? "Expand sidebar" : "Collapse sidebar";
}

function formatBytes(value) {
  const number = Number(value) || 0;
  if (!number) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(number) / Math.log(1024)));
  return `${(number / (1024 ** index)).toFixed(index ? 2 : 0)} ${units[index]}`;
}

function formatDate(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || !number) return "";
  const date = new Date(number < 10_000_000_000 ? number * 1000 : number);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function scrollToBottom(force = false) {
  if (!force && !state.stickToBottom) return;
  el.transcript.scrollTop = el.transcript.scrollHeight;
  el.jumpLatest.hidden = true;
}

function updateScrollState() {
  const distance = el.transcript.scrollHeight - el.transcript.scrollTop - el.transcript.clientHeight;
  state.stickToBottom = distance < 140;
  el.jumpLatest.hidden = state.stickToBottom || el.transcript.scrollHeight <= el.transcript.clientHeight + 20;
}

function currentConversationRow() {
  return state.conversations.find((item) => String(item.id) === String(state.currentConversationId)) || state.currentConversation;
}

function updateWindowTitle() {
  const row = currentConversationRow();
  el.windowConversationTitle.textContent = row?.title || "New chat";
}

function renderProjects() {
  const active = state.config.active_project_id || state.currentConversation?.project_id || "";
  for (const select of [el.projectSelect, $("#setActiveProject")]) {
    if (!select) continue;
    const previous = select.value || active;
    select.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "No project";
    select.append(none);
    for (const project of state.projects || []) {
      const option = document.createElement("option");
      option.value = String(project.id || "");
      option.textContent = project.name || project.title || project.id || "Project";
      select.append(option);
    }
    select.value = [...select.options].some((option) => option.value === previous) ? previous : "";
  }
}

function dateGroup(timestamp) {
  const number = Number(timestamp) || 0;
  const date = new Date(number < 10_000_000_000 ? number * 1000 : number);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  if (startDate >= startToday) return "Today";
  if (startDate >= startToday - 86400000) return "Yesterday";
  return "Earlier";
}

function renderConversations() {
  const query = el.conversationSearch.value.trim().toLowerCase();
  const rows = [...(state.conversations || [])]
    .filter((item) => !item.archived)
    .filter((item) => !query || String(item.title || "New chat").toLowerCase().includes(query))
    .sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)) || Number(b.updated_at || 0) - Number(a.updated_at || 0));

  el.conversationList.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-note hide-collapsed";
    empty.textContent = query ? "No chats match your search." : "No chats yet.";
    el.conversationList.append(empty);
    return;
  }

  let group = "";
  for (const row of rows) {
    const nextGroup = Boolean(row.pinned) ? "Pinned" : dateGroup(row.updated_at || row.created_at);
    if (nextGroup !== group) {
      group = nextGroup;
      const heading = document.createElement("div");
      heading.className = "conversation-group hide-collapsed";
      heading.textContent = group;
      el.conversationList.append(heading);
    }

    const item = document.createElement("div");
    item.className = `conversation-item ${String(row.id) === String(state.currentConversationId) ? "active" : ""}`;
    item.dataset.conversationId = row.id;

    const main = document.createElement("button");
    main.type = "button";
    main.className = "conversation-main";
    main.title = row.title || "New chat";
    main.innerHTML = `${icon(row.pinned ? "icon-pin" : "icon-message")}<span class="conversation-copy hide-collapsed"><span class="conversation-title"></span><span class="conversation-date">${esc(formatDate(row.updated_at || row.created_at))}</span></span>`;
    main.querySelector(".conversation-title").textContent = row.title || "New chat";
    main.addEventListener("click", () => selectConversation(row.id));

    const actions = document.createElement("div");
    actions.className = "conversation-actions hide-collapsed";
    const rename = document.createElement("button");
    rename.type = "button"; rename.className = "icon-button small"; rename.title = "Rename"; rename.innerHTML = icon("icon-pencil");
    rename.addEventListener("click", async () => {
      const title = await showPrompt({ title: "Rename chat", label: "Chat title", value: row.title || "", confirmLabel: "Rename", rows: 1 });
      if (title?.trim()) {
        const result = await api("rename_conversation", row.id, title.trim());
        if (result?.conversations) state.conversations = result.conversations;
        renderConversations(); updateWindowTitle();
      }
    });
    const pin = document.createElement("button");
    pin.type = "button"; pin.className = "icon-button small"; pin.title = row.pinned ? "Unpin" : "Pin"; pin.innerHTML = icon("icon-pin");
    pin.addEventListener("click", async () => {
      const result = await api("set_conversation_pinned", row.id, !row.pinned);
      if (result?.conversations) state.conversations = result.conversations;
      renderConversations();
    });
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "icon-button small"; remove.title = "Delete"; remove.innerHTML = icon("icon-trash");
    remove.addEventListener("click", async () => {
      const approved = await showLocalConfirm("Delete this chat?", "The conversation and its messages will be permanently removed from local history.", { title: row.title || "New chat" });
      if (!approved) return;
      const result = await api("delete_conversation", row.id);
      if (result?.conversations) {
        state.conversations = result.conversations;
        const next = result.current_conversation_id || state.conversations[0]?.id || "";
        if (next) await selectConversation(next, { force: true });
        else renderConversations();
      }
    });
    actions.append(rename, pin, remove);
    item.append(main, actions);
    el.conversationList.append(item);
  }
  updateWindowTitle();
}

function clearTranscript() {
  el.transcriptInner.replaceChildren();
  state.toolCards.clear();
  state.currentAssistantRow = null;
  state.currentToolStack = null;
  state.sources = [];
  renderSources([]);
}

function showEmptyState() {
  clearTranscript();
  if (el.emptyState) el.transcriptInner.append(el.emptyState);
}

function ensureAssistantRow() {
  if (state.currentAssistantRow && document.body.contains(state.currentAssistantRow)) return state.currentAssistantRow;
  if (el.emptyState?.parentElement === el.transcriptInner) el.emptyState.remove();
  const row = createMessageElement("assistant");
  el.transcriptInner.append(row);
  state.currentAssistantRow = row;
  state.currentToolStack = null;
  animateIn(row);
  scrollToBottom();
  return row;
}

function ensureToolStack(row = ensureAssistantRow()) {
  const article = row.querySelector(".message");
  let stack = article.querySelector(".tool-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "tool-stack";
    // Tool execution happens before the final assistant text in a turn, so
    // keep the live transcript in chronological order.
    article.insertBefore(stack, article.querySelector(".bubble"));
  }
  state.currentToolStack = stack;
  return stack;
}

function addMessage(role, content, options = {}) {
  if (el.emptyState?.parentElement === el.transcriptInner) el.emptyState.remove();
  const row = createMessageElement(role);
  if (options.messageId) row.dataset.messageId = options.messageId;
  updateMessageContent(row, {
    content,
    reasoning: options.reasoning || "",
    streaming: Boolean(options.streaming),
    incomplete: Boolean(options.incomplete),
  });
  if (role === "assistant" && !String(content || "").trim()) {
    row.querySelector(".bubble").style.display = "none";
  }
  el.transcriptInner.append(row);
  if (options.animate !== false) animateIn(row);
  if (role === "assistant") {
    state.currentAssistantRow = row;
    state.currentToolStack = null;
  }
  return row;
}

function revealAssistantBubble(row) {
  const bubble = row?.querySelector(".bubble");
  if (bubble) bubble.style.removeProperty("display");
}

function toolNameAndArgs(call) {
  if (!call || typeof call !== "object") return { name: "tool", args: {} };
  const fn = call.function || {};
  return {
    name: fn.name || call.name || "tool",
    args: parseArguments(fn.arguments ?? call.arguments ?? {}),
    id: call.id || call.tool_call_id || "",
  };
}

function renderMessages(messages = []) {
  clearTranscript();
  if (!messages.length) {
    if (el.emptyState) el.transcriptInner.append(el.emptyState);
    return;
  }

  let pendingAssistant = null;
  for (const message of messages) {
    if (message.role === "user") {
      pendingAssistant = null;
      addMessage("user", message.content || "", { animate: false, messageId: message.id });
    } else if (message.role === "assistant") {
      const calls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
      const row = addMessage("assistant", message.content || "", {
        animate: false,
        reasoning: message.reasoning || "",
        incomplete: message.incomplete,
        messageId: message.id,
      });
      pendingAssistant = row;
      if (calls.length) {
        const stack = ensureToolStack(row);
        for (const call of calls) {
          const { id, name, args } = toolNameAndArgs(call);
          const card = createToolCard({ id, name, arguments: args });
          stack.append(card);
          if (id) state.toolCards.set(String(id), card);
        }
      }
    } else if (message.role === "tool") {
      const id = String(message.tool_call_id || "");
      let card = state.toolCards.get(id);
      if (!card) {
        const host = pendingAssistant || addMessage("assistant", "", { animate: false });
        card = createToolCard({ id, name: message.name || "tool", arguments: {} });
        ensureToolStack(host).append(card);
        if (id) state.toolCards.set(id, card);
      }
      const parsed = parseArguments(message.content || "");
      finishToolCard(card, {
        content: message.content || "",
        raw: parsed,
        summary: typeof parsed === "object" && parsed?.summary ? parsed.summary : "Saved tool result",
        is_error: Boolean(typeof parsed === "object" && parsed?.error),
      });
      maybeRenderSystemStatus(card, { name: card.dataset.toolName, raw: parsed });
    }
  }
  const assistantRows = $$(".message-row.assistant", el.transcriptInner);
  state.currentAssistantRow = assistantRows.at(-1) || null;
  decorateAllCitations();
  scrollToBottom(true);
}

async function selectConversation(conversationId, options = {}) {
  if (!conversationId) return;
  if (state.streaming && !options.force) {
    showToast("Wait for the current response or stop it before switching chats.", "error");
    renderConversations();
    return;
  }
  const result = await api("get_conversation", conversationId);
  if (!result) return;
  state.currentConversationId = String(conversationId);
  state.currentConversation = result.conversation || null;
  if (state.currentConversation?.project_id !== undefined) {
    el.projectSelect.value = String(state.currentConversation.project_id || "");
  }
  renderMessages(result.messages || []);
  renderConversations();
  updateWindowTitle();
  el.composerInput.focus();
}

async function newChat() {
  if (state.streaming) {
    showToast("Stop the current response before starting another chat.", "error");
    return;
  }
  const projectId = el.projectSelect.value || "";
  if (!state.native) {
    state.currentConversationId = "";
    showEmptyState();
    el.composerInput.focus();
    return;
  }
  const result = await api("create_conversation", projectId);
  if (!result) return;
  if (result.conversations) state.conversations = result.conversations;
  state.currentConversation = result.conversation || null;
  state.currentConversationId = String(state.currentConversation?.id || "");
  renderMessages(result.messages || []);
  renderConversations();
  updateWindowTitle();
  el.composerInput.focus();
}

function renderAttachments() {
  el.contextChips.replaceChildren();
  state.attachments.forEach((attachment) => {
    const chip = document.createElement("span");
    chip.className = "context-chip";
    chip.innerHTML = `${icon("icon-paperclip")}<span></span><button type="button" aria-label="Remove attachment">${icon("icon-x")}</button>`;
    chip.querySelector("span").textContent = `${attachment.name || "Attachment"} · ${formatBytes(attachment.size)}`;
    chip.querySelector("button").addEventListener("click", async () => {
      const result = await api("remove_attachment", attachment.id);
      if (result?.attachments) state.attachments = result.attachments;
      else state.attachments = state.attachments.filter((item) => item.id !== attachment.id);
      renderAttachments();
      updateComposerMeta();
    });
    el.contextChips.append(chip);
  });
}

function updateComposerMeta() {
  const textTokens = Math.ceil(el.composerInput.value.length / 4);
  const attachmentTokens = Math.ceil(state.attachments.reduce((sum, item) => sum + (Number(item.size) || 0), 0) / 4);
  const total = textTokens + attachmentTokens;
  el.tokenCount.textContent = `~${total.toLocaleString()} tokens`;
}

function autoResizeComposer() {
  el.composerInput.style.height = "auto";
  const max = 8 * 23;
  el.composerInput.style.height = `${Math.min(Math.max(el.composerInput.scrollHeight, 24), max)}px`;
  el.composerInput.style.overflowY = el.composerInput.scrollHeight > max ? "auto" : "hidden";
}

function setStreaming(streaming) {
  state.streaming = Boolean(streaming);
  el.sendButton.classList.toggle("stop", state.streaming);
  el.sendButton.innerHTML = icon(state.streaming ? "icon-stop" : "icon-send");
  el.sendButton.title = state.streaming ? "Stop generating" : "Send";
  el.sendButton.setAttribute("aria-label", state.streaming ? "Stop generating" : "Send");
  el.attachButton.disabled = state.streaming;
}

function setEndpointChip(kind, label = "") {
  const cloud = String(kind).toLowerCase().includes("remote") || String(kind).toLowerCase().includes("cloud");
  el.modelChip.className = `model-chip ${cloud ? "cloud" : "local"}`;
  el.modelChip.textContent = cloud ? "CLOUD ○" : "LOCAL ●";
  el.modelChip.title = label || el.modelChip.textContent;
}

async function stopGeneration() {
  if (!state.streaming) return;
  await api("stop_generation");
  showToast("Stopping generation…");
}

async function sendText(rawText) {
  const text = String(rawText || "").trim();
  if (!text) return;
  if (await handleUiCommand(text)) {
    el.composerInput.value = "";
    autoResizeComposer(); updateComposerMeta();
    return;
  }
  if (!state.native) {
    showToast("Open VISION through the desktop shell to send messages.", "error");
    return;
  }
  if (state.streaming) return;

  addMessage("user", text);
  const assistant = ensureAssistantRow();
  updateMessageContent(assistant, { content: "", reasoning: "", streaming: true });
  revealAssistantBubble(assistant);
  state.sources = [];
  renderSources([]);
  const attachmentIds = state.attachments.map((item) => item.id);
  el.composerInput.value = "";
  autoResizeComposer(); updateComposerMeta();
  setStreaming(true);
  scrollToBottom(true);

  const result = await api("send_message", {
    text,
    conversation_id: state.currentConversationId || "",
    project_id: el.projectSelect.value || "",
    attachment_ids: attachmentIds,
  });
  if (!result?.accepted) {
    setStreaming(false);
    updateMessageContent(assistant, { content: result?.message || "The message could not be started.", streaming: false, incomplete: true });
    return;
  }
  state.attachments = [];
  renderAttachments();
}

async function handleUiCommand(text) {
  const command = text.trim().toLowerCase();
  if (command === "/settings") { openSettings(); return true; }
  if (command === "/server") { openServer(); return true; }
  if (command === "/activity") { openActivity(); return true; }
  if (command === "/new") { await newChat(); return true; }
  if (command === "/theme") { await toggleTheme(); return true; }
  if (command === "/help") { openPalette(); return true; }
  if (command === "/regenerate") { await regenerate(); return true; }
  return false;
}

async function regenerate() {
  if (!state.native || state.streaming || !state.currentConversationId) return;
  const rows = $$(".message-row", el.transcriptInner);
  let lastUserIndex = -1;
  rows.forEach((row, index) => { if (row.classList.contains("user")) lastUserIndex = index; });
  if (lastUserIndex >= 0) rows.slice(lastUserIndex + 1).forEach((row) => row.remove());
  state.toolCards.clear();
  const assistant = ensureAssistantRow();
  updateMessageContent(assistant, { content: "", reasoning: "", streaming: true });
  revealAssistantBubble(assistant);
  setStreaming(true);
  const result = await api("regenerate", state.currentConversationId);
  if (!result?.accepted) {
    setStreaming(false);
    updateMessageContent(assistant, { content: result?.message || "Could not regenerate the last response.", streaming: false, incomplete: true });
  }
}

function maybeRenderSystemStatus(card, result) {
  if (!card || result?.name !== "system_status") return;
  const raw = parseArguments(result.raw ?? result.content ?? {});
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return;
  const gpuDevices = Array.isArray(raw.gpu?.devices) ? raw.gpu.devices : [];
  const gpuText = gpuDevices.length
    ? gpuDevices.map((gpu) => `${gpu.name}: ${Math.round(gpu.vram_used_mb || 0)} / ${Math.round(gpu.vram_total_mb || 0)} MB VRAM`).join("\n")
    : "No NVIDIA GPU telemetry available";
  const widget = renderGenUI({
    kind: "cards",
    title: "System status",
    cards: [
      { title: "CPU", value: `${Number(raw.cpu_percent || 0).toFixed(1)}%`, body: `${raw.cpu_count || "—"} logical processors` },
      { title: "RAM", value: `${Number(raw.ram_percent || 0).toFixed(1)}%`, body: `${raw.ram_used_gb || 0} GB used of ${raw.ram_total_gb || 0} GB` },
      { title: "Disk", value: `${Number(raw.disk_used_percent || 0).toFixed(1)}%`, body: `${raw.disk_free_gb || 0} GB free of ${raw.disk_total_gb || 0} GB on ${raw.disk_root || ""}` },
      { title: "GPU", value: gpuDevices.length ? `${Number(gpuDevices[0].utilization_percent || 0).toFixed(0)}%` : "—", body: gpuText },
    ],
  });
  if (card._widgetSlot) card._widgetSlot.replaceChildren(widget);
}

function finalizeStreamingRow(payload = {}) {
  const row = ensureAssistantRow();
  let content = payload.assistant_text ?? row.querySelector(".message")?._content ?? "";
  if (payload.error && !String(content).trim()) {
    content = `Something went wrong:\n\n\`${String(payload.error).replaceAll("`", "'")}\``;
  }
  if (payload.cancelled && !String(content).trim()) content = "Generation stopped.";
  updateMessageContent(row, {
    content,
    reasoning: payload.reasoning ?? row.querySelector(".message")?._reasoning ?? "",
    streaming: false,
    incomplete: Boolean(payload.incomplete || payload.error || payload.cancelled),
  });
  if (String(content).trim()) revealAssistantBubble(row);
  decorateCitations(row);
}

function decorateCitations(root) {
  const markdown = root.querySelector?.(".markdown") || root;
  if (!markdown) return;
  const walker = document.createTreeWalker(markdown, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!/\[(?:S|L)\d+\]/.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
      let parent = node.parentElement;
      while (parent && parent !== markdown) {
        if (["CODE", "PRE", "A", "BUTTON"].includes(parent.tagName)) return NodeFilter.FILTER_REJECT;
        parent = parent.parentElement;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const fragment = document.createDocumentFragment();
    const parts = node.nodeValue.split(/(\[(?:S|L)\d+\])/g);
    for (const part of parts) {
      const match = part.match(/^\[((?:S|L)\d+)\]$/);
      if (!match) { fragment.append(document.createTextNode(part)); continue; }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "citation-chip";
      button.dataset.sourceId = match[1];
      button.textContent = match[1];
      button.title = `Open source ${match[1]}`;
      fragment.append(button);
    }
    node.replaceWith(fragment);
  }
}

function decorateAllCitations() {
  $$(".message-row.assistant", el.transcriptInner).forEach(decorateCitations);
}

function renderSources(sources = []) {
  state.sources = Array.isArray(sources) ? sources : [];
  el.sourcesList.replaceChildren();
  if (!state.sources.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "Sources used in the current answer will appear here.";
    el.sourcesList.append(note);
    return;
  }
  for (const source of state.sources) {
    const card = document.createElement("article");
    card.className = "source-card";
    card.dataset.sourceId = source.source_id || source.id || "";
    const top = document.createElement("div");
    top.className = "source-top";
    const badge = document.createElement("span");
    badge.className = "source-id";
    badge.textContent = `[${source.source_id || source.id || "S"}]`;
    const open = document.createElement("button");
    open.type = "button"; open.className = "icon-button small"; open.title = "Open source"; open.innerHTML = icon("icon-external");
    open.addEventListener("click", () => openSource(source));
    top.append(badge, open);
    const title = document.createElement("h3");
    title.textContent = source.title || source.url || source.path || "Source";
    const meta = document.createElement("div");
    meta.className = "source-meta";
    meta.textContent = source.locator || source.fetched_at || source.path || "";
    const snippet = document.createElement("p");
    snippet.textContent = source.snippet || "";
    card.append(top, title);
    if (meta.textContent) card.append(meta);
    if (snippet.textContent) card.append(snippet);
    el.sourcesList.append(card);
  }
}

async function openSource(source) {
  if (source.url) await callOptional("open_url", { ok: false }, source.url);
  else if (source.path) await callOptional("open_source_path", { ok: false }, source.path);
}

function openInspector(sourceId = "") {
  el.inspector.classList.add("open");
  if (sourceId) {
    const card = el.sourcesList.querySelector(`[data-source-id="${CSS.escape(sourceId)}"]`);
    if (card) {
      $$(".source-card", el.sourcesList).forEach((item) => item.classList.remove("highlight"));
      card.classList.add("highlight");
      card.scrollIntoView({ block: "nearest" });
      window.setTimeout(() => card.classList.remove("highlight"), 900);
    }
  }
}

function dlRows(rows) {
  return rows.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value ?? "—")}</dd>`).join("");
}

function renderServer(payload = {}) {
  const server = payload.server || payload;
  if (!server || typeof server !== "object") return;
  state.server = { ...state.server, ...server };
  const healthy = server.healthy === true;
  const warning = Boolean(server.running || /starting|unhealthy|not checked/i.test(server.state || ""));
  el.serverDot.className = `status-dot ${healthy ? "healthy" : warning ? "warn" : "error"}`;
  el.serverPillText.textContent = server.state || (healthy ? "Local server healthy" : "Server not checked");

  const modelStatus = server.model_exists
    ? `${formatBytes(server.model_size)} installed`
    : "Missing";
  const build = server.build ? `b${server.build}` : "Unknown";
  const rows = [
    ["State", server.state || "—"],
    ["Process", server.running ? "Running" : "Stopped"],
    ["Local URL", server.local_base_url || state.config.local_base_url || "http://127.0.0.1:8080/v1"],
    ["Health failures", server.failures ?? 0],
    ["Model", modelStatus],
    ["Model path", server.model_path || "—"],
    ["llama-server", server.binary || "Not found"],
    ["Build", `${build} (minimum b${server.minimum_build || 11815}, ${server.build_supported ? "supported" : "not supported"})`],
    ["Command", Array.isArray(server.command) ? server.command.join(" ") : "—"],
    ["Exit code", server.exit_code ?? "—"],
  ];
  const serverStatus = $("#serverStatus");
  if (serverStatus) serverStatus.innerHTML = dlRows(rows);
  const modelStatusEl = $("#modelStatus");
  if (modelStatusEl) modelStatusEl.innerHTML = dlRows(rows.slice(0, 8));
  const setupStatus = $("#setupStatus");
  if (setupStatus) setupStatus.innerHTML = dlRows([
    ["Model", modelStatus],
    ["Expected size", formatBytes(server.expected_model_size)],
    ["llama-server", server.binary || "Not found"],
    ["Build", `${build} (${server.build_supported ? "supported" : "needs b11815+"})`],
    ["Server", server.state || "Not checked"],
  ]);
  if (Array.isArray(server.logs)) setServerLogs(server.logs);
  else if (typeof server.logs === "string") setServerLogs(server.logs.split("\n"));
}

const serverLogLines = [];
function setServerLogs(lines) {
  serverLogLines.splice(0, serverLogLines.length, ...lines.slice(-300));
  const target = $("#serverLog");
  if (target) {
    target.textContent = serverLogLines.join("\n");
    target.scrollTop = target.scrollHeight;
  }
}

function appendServerLog(line) {
  serverLogLines.push(String(line || ""));
  while (serverLogLines.length > 300) serverLogLines.shift();
  setServerLogs(serverLogLines);
}

function showOverlay(id) {
  const overlay = $(id);
  overlay.hidden = false;
  const sheet = overlay.querySelector(".sheet, .palette");
  if (sheet) animateSheet(sheet);
  const focusable = overlay.querySelector("input, textarea, select, button:not([disabled])");
  window.setTimeout(() => focusable?.focus(), 30);
  return overlay;
}

function hideOverlay(id) {
  const overlay = $(id);
  if (overlay) overlay.hidden = true;
}

function renderSettings(data = {}) {
  if (data.config) state.config = { ...state.config, ...data.config };
  if (data.credentials) state.credentials = data.credentials;
  if (data.paths) state.paths = { ...state.paths, ...data.paths };
  if (data.projects) state.projects = data.projects;
  if (data.skills) state.skills = data.skills;
  if (data.extensions) state.extensions = data.extensions;
  if (data.server) renderServer(data.server);

  const c = state.config || {};
  $("#setTheme").value = c.theme || "dark";
  $("#setEndpointMode").value = c.endpoint_mode || "auto";
  $("#setMaxTools").value = c.max_extension_tools ?? 32;
  $("#setAutoStart").checked = Boolean(c.auto_start_local_server);
  $("#setModelPath").value = c.model_path || state.paths.model_file || "";
  $("#setServerPath").value = c.llama_server_path || "";
  $("#setLocalPort").value = c.local_port ?? 8080;
  $("#setContextSize").value = c.context_size ?? 16384;
  $("#setGpuLayers").value = c.gpu_layers ?? 99;
  $("#setGpuLayersValue").textContent = c.gpu_layers ?? 99;
  $("#setupGpu").value = c.gpu_layers ?? 99;
  $("#setupGpuValue").textContent = c.gpu_layers ?? 99;
  $("#setLocalModel").value = c.local_model_override || "";
  $("#setRemoteUrl").value = c.remote_base_url || "";
  $("#setRemoteModel").value = c.remote_model_override || "";
  $("#setTelemetry").checked = Boolean(c.telemetry_opt_in);
  renderProjects();
  $("#setActiveProject").value = c.active_project_id || "";

  const remoteKey = $("#setRemoteKey");
  remoteKey.value = "";
  remoteKey.placeholder = state.credentials.remote_api_key_stored ? "Stored in Credential Manager" : "Not stored";
  $("#clearRemoteKey").checked = false;
  $("#clearRemoteKey").disabled = !state.credentials.remote_api_key_stored;
  const hfToken = $("#setHfToken");
  hfToken.value = "";
  hfToken.placeholder = state.credentials.hf_token_stored ? "Stored in Credential Manager" : "Not stored";
  $("#clearHfToken").checked = false;
  $("#clearHfToken").disabled = !state.credentials.hf_token_stored;

  $("#settingsAppData").textContent = state.paths.app_data || "—";
  $("#settingsWorkspace").textContent = state.paths.workspace || "—";
  $("#aboutVersion").textContent = state.app.version || "—";
  $("#aboutBuild").textContent = state.app.build || "source";
  renderSkills();
  renderExtensions();
}

function renderSkills() {
  const target = $("#skillsList");
  target.replaceChildren();
  if (!state.skills?.length) {
    target.innerHTML = '<p class="empty-note">No skills installed. Import a SKILL.md folder, Markdown file, .zip or ZIP-based .skill package.</p>';
    return;
  }
  for (const skill of state.skills) {
    const row = document.createElement("article");
    row.className = "extension-card";
    const main = document.createElement("div");
    const title = document.createElement("h4");
    title.textContent = skill.name;
    const desc = document.createElement("p");
    desc.textContent = skill.description || "No description";
    const meta = document.createElement("div");
    meta.className = "extension-meta";
    meta.textContent = `${skill.files?.length || 0} files · ${skill.scripts?.length || 0} scripts · SHA ${String(skill.sha256 || "").slice(0, 10)}`;
    main.append(title, desc, meta);
    const label = document.createElement("label");
    label.className = "switch-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(skill.enabled);
    checkbox.dataset.skillName = skill.name;
    label.append(checkbox, document.createTextNode("Enabled"));
    row.append(main, label);
    target.append(row);
  }
}

function renderExtensions() {
  const target = $("#extensionsList");
  if (!target) return;
  target.replaceChildren();
  const groups = [
    ["mcp", state.extensions.servers || []],
    ["connector", state.extensions.connectors || []],
  ];
  let count = 0;
  for (const [kind, items] of groups) {
    for (const item of items) {
      count += 1;
      const card = document.createElement("article");
      card.className = "extension-card";
      const main = document.createElement("div");
      const title = document.createElement("h4");
      title.textContent = item.display_name || item.id;
      const desc = document.createElement("p");
      desc.textContent = item.url || [item.command, ...(item.args || [])].filter(Boolean).join(" ") || item.description || "No endpoint/command shown";
      const meta = document.createElement("div");
      meta.className = "extension-meta";
      const dynamic = (state.extensions.dynamic_tools || []).filter((name) => String(name).includes(item.id));
      meta.textContent = `${kind.toUpperCase()} · ${item.transport || "remote"} · ${item.enabled ? "enabled" : "disabled"} · ${item.toolset_sha256 ? "trusted toolset" : "not trusted"} · ${dynamic.length} active tools`;
      const tools = document.createElement("div");
      tools.className = "extension-tools";
      tools.textContent = dynamic.length ? dynamic.join(", ") : "Discover and trust the exact toolset before its tools are sent to the model.";
      main.append(title, desc, meta, tools);
      const actions = document.createElement("div");
      actions.className = "extension-actions";
      const discover = document.createElement("button");
      discover.type = "button"; discover.className = "button small"; discover.textContent = "Discover / trust";
      discover.addEventListener("click", async () => {
        discover.disabled = true;
        showToast(`Discovering ${item.display_name || item.id}…`);
        await api("discover_extension", kind, item.id);
        window.setTimeout(() => { discover.disabled = false; }, 1000);
      });
      actions.append(discover);
      card.append(main, actions);
      target.append(card);
    }
  }
  if (!count) target.innerHTML = '<p class="empty-note">No MCP servers or connectors configured.</p>';
  $("#extensionsMessages").textContent = (state.extensions.messages || []).join("\n");
}

async function openSettings(section = "general") {
  showOverlay("#settingsOverlay");
  activateSettingsTab(section);
  if (state.native) {
    const data = await api("get_settings");
    if (data) renderSettings(data);
  } else {
    renderSettings({});
  }
}

function activateSettingsTab(section) {
  $$("[data-settings-tab]").forEach((button) => button.classList.toggle("active", button.dataset.settingsTab === section));
  $$("[data-settings-section]").forEach((panel) => { panel.hidden = panel.dataset.settingsSection !== section; });
}

async function saveSettings() {
  const enabledSkills = $$("#skillsList input[type='checkbox']:checked").map((input) => input.dataset.skillName).filter(Boolean);
  const payload = {
    theme: $("#setTheme").value,
    endpoint_mode: $("#setEndpointMode").value,
    active_project_id: $("#setActiveProject").value,
    max_extension_tools: Number($("#setMaxTools").value || 0),
    auto_start_local_server: $("#setAutoStart").checked,
    model_path: $("#setModelPath").value.trim(),
    llama_server_path: $("#setServerPath").value.trim(),
    local_port: Number($("#setLocalPort").value || 8080),
    context_size: Number($("#setContextSize").value || 16384),
    gpu_layers: Number($("#setGpuLayers").value || 0),
    local_model_override: $("#setLocalModel").value.trim(),
    remote_base_url: $("#setRemoteUrl").value.trim(),
    remote_model_override: $("#setRemoteModel").value.trim(),
    telemetry_opt_in: $("#setTelemetry").checked,
    enabled_skills: enabledSkills,
    remote_api_key: $("#setRemoteKey").value,
    clear_remote_api_key: $("#clearRemoteKey").checked,
    hf_token: $("#setHfToken").value,
    clear_hf_token: $("#clearHfToken").checked,
  };
  if (!state.native) {
    setTheme(payload.theme);
    hideOverlay("#settingsOverlay");
    showToast("Theme saved locally in preview mode.", "success");
    return;
  }
  const result = await api("save_settings", payload);
  if (!result) return;
  renderSettings(result);
  el.projectSelect.value = payload.active_project_id || "";
  setTheme(payload.theme);
  hideOverlay("#settingsOverlay");
  showToast("Settings saved.", "success");
  if (result.restart_needed) showBanner("Model/server settings changed. Restart the local server from the Server console to apply them.");
}

async function openServer() {
  showOverlay("#serverOverlay");
  if (state.native) {
    const result = await api("get_server_state");
    if (result) renderServer(result);
  }
}

async function openActivity() {
  showOverlay("#activityOverlay");
  if (!state.native) return;
  const data = await api("get_activity");
  if (!data) return;
  $("#auditLog").textContent = data.audit_text || "No audit entries yet.";
  $("#feedbackLog").textContent = (data.feedback_rows || []).map((row) => JSON.stringify(row)).join("\n") || "Feedback queue is empty.";
  $("#feedbackCount").textContent = data.feedback_count ? `(${data.feedback_count} queued)` : "";
}

function showPrompt({ title = "Input", label = "Value", value = "", confirmLabel = "Save", rows = 5 } = {}) {
  return new Promise((resolve) => {
    promptResolver = resolve;
    $("#promptTitle").textContent = title;
    $("#promptLabel").textContent = label;
    const input = $("#promptInput");
    input.rows = rows;
    input.value = value;
    $("#promptConfirm").textContent = confirmLabel;
    showOverlay("#promptOverlay");
    input.select();
  });
}

function resolvePrompt(value) {
  hideOverlay("#promptOverlay");
  if (promptResolver) promptResolver(value);
  promptResolver = null;
}

function presentConfirmation(item) {
  activeConfirmation = item;
  $("#confirmationTitle").textContent = item.title || "Allow?";
  $("#confirmationMessage").textContent = item.message || "";
  $("#confirmationDetails").textContent = typeof item.details === "string" ? item.details : JSON.stringify(item.details || {}, null, 2);
  $("#confirmationApprove").textContent = item.approveLabel || "Allow";
  $("#confirmationDeny").textContent = item.denyLabel || "Deny";
  showOverlay("#confirmationOverlay");
  window.setTimeout(() => $("#confirmationApprove").focus(), 40);
}

function enqueueConfirmation(item) {
  confirmationQueue.push(item);
  if (!activeConfirmation) presentConfirmation(confirmationQueue.shift());
}

async function resolveActiveConfirmation(approved) {
  const item = activeConfirmation;
  if (!item) return;
  activeConfirmation = null;
  hideOverlay("#confirmationOverlay");
  if (item.kind === "native") await callOptional("resolve_confirmation", { ok: false }, item.id, Boolean(approved));
  else item.resolve(Boolean(approved));
  if (confirmationQueue.length) presentConfirmation(confirmationQueue.shift());
}

function showLocalConfirm(title, message, details, options = {}) {
  return new Promise((resolve) => enqueueConfirmation({ kind: "local", title, message, details, resolve, ...options }));
}

function updateTaskProgress(payload = {}) {
  const status = $("#taskStatus");
  const line = $("#taskProgress");
  if (status) {
    const detail = payload.percent !== null && payload.percent !== undefined ? ` ${payload.percent}%` : "";
    const bytes = payload.total ? ` (${formatBytes(payload.downloaded)} / ${formatBytes(payload.total)})` : "";
    status.textContent = `${payload.message || payload.name || "Working…"}${detail}${bytes}`;
  }
  if (line) {
    line.hidden = payload.percent === null || payload.percent === undefined;
    const fill = line.querySelector("i");
    if (fill) fill.style.width = `${Math.max(0, Math.min(100, Number(payload.percent) || 0))}%`;
  }
}

async function handleTaskFinished(payload = {}) {
  updateTaskProgress({ message: "", percent: null });
  const line = $("#taskProgress"); if (line) line.hidden = true;
  if (!payload.ok) {
    showToast(payload.error || `${payload.name || "Task"} failed`, "error");
    const status = $("#taskStatus"); if (status) status.textContent = payload.error || "Task failed";
    return;
  }
  const result = payload.result || {};
  const message = result.message || "Done";
  const status = $("#taskStatus"); if (status) status.textContent = message;
  showToast(message, "success");
  if (result.server) renderServer(result.server);
  if (result.config || result.skills) {
    if (result.skills) state.skills = result.skills;
    renderSettings(result);
  }
  if (String(payload.name || "").startsWith("discover_extension:")) {
    const toolNames = (result.tools || []).map((tool) => tool.name || tool.title || "").filter(Boolean);
    const approved = await showLocalConfirm(
      `Trust ${result.display_name || result.id}?`,
      "These exact tool definitions will be fingerprinted. If the server later changes any name, description, schema, or annotations, VISION will require trust again. Individual tools default to asking before execution.",
      { fingerprint: result.fingerprint, tools: result.tools || [] },
      { approveLabel: "Trust toolset", denyLabel: "Cancel" },
    );
    if (approved) {
      await api("trust_extension", result.kind, result.id, result.fingerprint, toolNames);
      showToast("Extension toolset trusted. Refreshing tools…", "success");
    }
  }
  if (payload.name === "check_model_update") {
    const statusText = result.status === "current" ? "The installed model is current." : `Model update check: ${result.status || "complete"}`;
    showToast(statusText, "success");
  }
  if (payload.name === "upload_feedback" && !$("#activityOverlay").hidden) openActivity();
}

function registerBridgeEvents() {
  on("content_delta", (payload) => {
    const row = ensureAssistantRow();
    revealAssistantBubble(row);
    appendMessageContent(row, payload.text || "");
    scrollToBottom();
  });
  on("reasoning_delta", (payload) => {
    const row = ensureAssistantRow();
    appendMessageReasoning(row, payload.text || "");
    scrollToBottom();
  });
  on("tool_started", (payload) => {
    const row = ensureAssistantRow();
    const stack = ensureToolStack(row);
    const card = createToolCard({
      id: payload.tool_call_id || payload.id || "",
      name: payload.name || "tool",
      arguments: payload.arguments ?? payload.args ?? {},
    });
    stack.append(card);
    if (card.dataset.toolId) state.toolCards.set(card.dataset.toolId, card);
    animateIn(card);
    scrollToBottom();
  });
  on("tool_finished", (payload) => {
    const id = String(payload.tool_call_id || payload.id || "");
    let card = state.toolCards.get(id);
    if (!card) {
      card = createToolCard({ id, name: payload.name || "tool", arguments: payload.arguments || {} });
      ensureToolStack(ensureAssistantRow()).append(card);
      if (id) state.toolCards.set(id, card);
    }
    finishToolCard(card, payload);
    maybeRenderSystemStatus(card, payload);
    scrollToBottom();
  });
  on("endpoint", (payload) => {
    state.lastEndpoint = payload;
    setEndpointChip(payload.kind || payload.label || "", payload.label || "");
    if (payload.failed_over) showBanner("Local endpoint failed, so this request used the configured cloud endpoint.");
    else if (payload.failover_offered) showBanner("Local endpoint is unavailable. Cloud failover is available in Auto mode.");
  });
  on("sources", (payload) => {
    renderSources(payload.sources || payload || []);
    if (state.sources.length && state.currentAssistantRow) {
      const actions = state.currentAssistantRow.querySelector(".message-actions");
      if (actions && !actions.querySelector(".sources-button")) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "button small ghost sources-button";
        button.textContent = "Sources";
        button.addEventListener("click", () => openInspector());
        actions.append(button);
      }
    }
  });
  on("confirmation_requested", (payload) => {
    enqueueConfirmation({
      kind: "native",
      id: payload.id,
      title: payload.title || "Allow this action?",
      message: payload.message || "VISION is asking for approval before continuing.",
      details: payload.details || {},
      approveLabel: "Allow",
      denyLabel: "Deny",
    });
  });
  on("turn_finished", (payload) => {
    if (payload.conversation_id) state.currentConversationId = String(payload.conversation_id);
    if (Array.isArray(payload.conversations)) state.conversations = payload.conversations;
    if (Array.isArray(payload.sources) && payload.sources.length) renderSources(payload.sources);
    finalizeStreamingRow(payload);
    setStreaming(false);
    renderConversations();
    updateWindowTitle();
    scrollToBottom();
    el.composerInput.focus();
  });
  on("server_state", renderServer);
  on("server_log", (payload) => appendServerLog(payload.line || ""));
  on("banner", (payload) => showBanner(payload.message || ""));
  on("toast", (payload) => showToast(payload.message || "", payload.kind || "info"));
  on("error", (payload) => showToast(payload.message || "Something went wrong", "error"));
  on("task_started", (payload) => updateTaskProgress({ name: payload.name, message: "Working…", percent: null }));
  on("task_progress", updateTaskProgress);
  on("task_finished", handleTaskFinished);
  on("extensions_updated", (payload) => {
    state.extensions = payload;
    renderExtensions();
  });
  on("timer_scheduled", (payload) => showToast(`Timer set: ${payload.label || "Timer"} (${payload.minutes} min)`, "success"));
  on("timer_fired", (payload) => {
    showBanner(`Timer finished: ${payload.label || "Timer"}`);
    showToast(`Timer finished: ${payload.label || "Timer"}`, "success");
  });
}

function paletteCommands() {
  const commands = [
    { title: "New chat", hint: "Start a fresh conversation", run: newChat },
    { title: "Regenerate last response", hint: "Ctrl+Enter", run: regenerate },
    { title: "Stop generation", hint: "Esc while streaming", run: stopGeneration },
    { title: "Attach text files", hint: "Native file picker", run: chooseAttachments },
    { title: "Export conversation", hint: "Save Markdown", run: async () => { if (state.currentConversationId) await api("export_conversation", state.currentConversationId); } },
    { title: "Open settings", hint: "General", run: () => openSettings("general") },
    { title: "Model & server settings", hint: "Downloads, GPU layers, paths", run: () => openSettings("model") },
    { title: "Endpoint settings", hint: "Local/cloud routing", run: () => openSettings("endpoint") },
    { title: "Extensions", hint: "Skills, MCP, connectors", run: () => openSettings("extensions") },
    { title: "Server console", hint: "Logs and lifecycle", run: openServer },
    { title: "Start local server", hint: "llama-server", run: () => api("start_server") },
    { title: "Restart local server", hint: "llama-server", run: () => api("restart_server") },
    { title: "Stop local server", hint: "llama-server", run: () => api("stop_server") },
    { title: "Activity & feedback", hint: "Audit log and feedback queue", run: openActivity },
    { title: "Toggle theme", hint: "Dark / light", run: toggleTheme },
    { title: "Toggle sidebar", hint: "264px / 64px", run: () => setSidebarCollapsed(!el.workspace.classList.contains("sidebar-collapsed")) },
    { title: "Show sources", hint: "Current answer citations", run: () => openInspector() },
  ];
  const chats = state.conversations.map((row) => ({
    title: row.title || "New chat",
    hint: "Open chat",
    run: () => selectConversation(row.id),
  }));
  return [...commands, ...chats];
}

function renderPalette(query = "") {
  const list = $("#paletteList");
  list.replaceChildren();
  const normalized = query.trim().toLowerCase();
  const matches = paletteCommands().filter((item) => !normalized || `${item.title} ${item.hint}`.toLowerCase().includes(normalized)).slice(0, 14);
  if (!matches.length) {
    list.innerHTML = '<p class="empty-note" style="padding:10px">No matching command.</p>';
    return;
  }
  matches.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `palette-item ${index === 0 ? "selected" : ""}`;
    button.innerHTML = `<span></span><span class="palette-hint"></span>`;
    button.children[0].textContent = item.title;
    button.children[1].textContent = item.hint;
    button.addEventListener("click", async () => {
      hideOverlay("#paletteOverlay");
      await item.run();
    });
    list.append(button);
  });
}

function openPalette() {
  const input = $("#paletteInput");
  input.value = "";
  renderPalette("");
  showOverlay("#paletteOverlay");
  input.focus();
}

async function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(next);
  if (state.native) await api("save_settings", { theme: next });
}

async function chooseAttachments() {
  if (!state.native) {
    showToast("Attachments require the desktop shell.", "error");
    return;
  }
  const result = await api("choose_attachments");
  if (result?.attachments) {
    state.attachments = result.attachments;
    renderAttachments();
    updateComposerMeta();
  }
}

async function copyText(text) {
  const result = await callOptional("copy_text", null, String(text || ""));
  if (!result?.ok) {
    try { await navigator.clipboard.writeText(String(text || "")); }
    catch (_error) { showToast("Could not copy text.", "error"); return; }
  }
  showToast("Copied.", "success");
}

function bindUi() {
  bindPressFeedback(document);

  $("#windowMinimize").addEventListener("click", () => callOptional("window_minimize", { ok: false }));
  $("#windowMaximize").addEventListener("click", () => callOptional("window_maximize", { ok: false }));
  $("#windowClose").addEventListener("click", () => callOptional("window_close", { ok: false }));
  $(".titlebar").addEventListener("dblclick", (event) => {
    if (!event.target.closest("button")) callOptional("window_maximize", { ok: false });
  });

  $("#sidebarToggle").addEventListener("click", () => setSidebarCollapsed(!el.workspace.classList.contains("sidebar-collapsed")));
  $("#newChatButton").addEventListener("click", newChat);
  el.conversationSearch.addEventListener("input", renderConversations);
  el.projectSelect.addEventListener("change", async () => {
    const result = await api("set_active_project", el.projectSelect.value, state.currentConversationId || "");
    if (result?.projects) state.projects = result.projects;
    state.config.active_project_id = el.projectSelect.value;
    renderProjects();
  });

  $("#serverPill").addEventListener("click", openServer);
  $("#openServerButton").addEventListener("click", openServer);
  $("#openActivityButton").addEventListener("click", openActivity);
  $("#openSettingsButton").addEventListener("click", () => openSettings("general"));
  $("#themeToggle").addEventListener("click", toggleTheme);
  $("#bannerClose").addEventListener("click", hideBanner);
  $("#closeInspector").addEventListener("click", () => el.inspector.classList.remove("open"));
  el.jumpLatest.addEventListener("click", () => scrollToBottom(true));
  el.transcript.addEventListener("scroll", updateScrollState, { passive: true });

  el.transcriptInner.addEventListener("click", async (event) => {
    const suggestion = event.target.closest("[data-suggestion]");
    if (suggestion) {
      el.composerInput.value = suggestion.dataset.suggestion;
      autoResizeComposer(); updateComposerMeta();
      await sendText(suggestion.dataset.suggestion);
      return;
    }
    const citation = event.target.closest(".citation-chip");
    if (citation) { openInspector(citation.dataset.sourceId); return; }
    const copyCode = event.target.closest(".copy-code");
    if (copyCode) {
      const code = copyCode.closest(".code-block")?.querySelector("code")?.textContent || "";
      await copyText(code);
      return;
    }
    const link = event.target.closest("a[href]");
    if (link && state.native) {
      event.preventDefault();
      await api("open_url", link.href);
      return;
    }
    const actionButton = event.target.closest("[data-action]");
    if (!actionButton) return;
    const row = actionButton.closest(".message-row");
    const article = row?.querySelector(".message");
    const action = actionButton.dataset.action;
    if (action === "copy-message") await copyText(article?._content || "");
    if (action === "regenerate-message") await regenerate();
    if (action === "feedback-message") {
      const result = await api("record_feedback", state.currentConversationId, "Thumbs down from web UI");
      if (result?.ok) showToast("Saved to the local feedback queue.", "success");
    }
    if (action === "correct-message") {
      const correction = await showPrompt({ title: "Correct this answer", label: "What should VISION have said or done?", value: "", confirmLabel: "Save correction", rows: 6 });
      if (correction?.trim()) await sendText(`/fix ${correction.trim()}`);
    }
  });

  el.attachButton.addEventListener("click", chooseAttachments);
  el.composerInput.addEventListener("input", () => { autoResizeComposer(); updateComposerMeta(); });
  el.composerInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      el.composerForm.requestSubmit();
    }
  });
  el.composerForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.streaming) await stopGeneration();
    else await sendText(el.composerInput.value);
  });

  $$("[data-settings-tab]").forEach((button) => button.addEventListener("click", () => activateSettingsTab(button.dataset.settingsTab)));
  $$("[data-close-settings]").forEach((button) => button.addEventListener("click", () => { setTheme(state.config.theme || "dark"); hideOverlay("#settingsOverlay"); }));
  $("#settingsSave").addEventListener("click", saveSettings);
  $("#setGpuLayers").addEventListener("input", (event) => { $("#setGpuLayersValue").textContent = event.target.value; });
  $("#setupGpu").addEventListener("input", (event) => { $("#setupGpuValue").textContent = event.target.value; });
  $("#setTheme").addEventListener("change", (event) => setTheme(event.target.value, false));
  $$("[data-pick-file]").forEach((button) => button.addEventListener("click", async () => {
    const result = await api("pick_file", button.dataset.pickFile);
    if (!result?.path) return;
    if (button.dataset.pickFile === "model") $("#setModelPath").value = result.path;
    if (button.dataset.pickFile === "server") $("#setServerPath").value = result.path;
  }));
  $$("[data-task]").forEach((button) => button.addEventListener("click", async () => {
    const map = { "download-model": "download_model", "download-llama": "download_llama", "check-update": "check_model_update" };
    const method = map[button.dataset.task];
    if (method) await api(method);
  }));
  $("#refreshExtensions").addEventListener("click", () => api("refresh_extensions"));
  $("#importSkillFile").addEventListener("click", () => api("import_skill", false));
  $("#importSkillFolder").addEventListener("click", () => api("import_skill", true));

  $$("[data-close-server]").forEach((button) => button.addEventListener("click", () => hideOverlay("#serverOverlay")));
  $$("[data-server-action]").forEach((button) => button.addEventListener("click", async () => {
    const action = button.dataset.serverAction;
    if (action === "refresh") { const result = await api("get_server_state"); if (result) renderServer(result); }
    else await api(`${action}_server`);
  }));

  $$("[data-close-activity]").forEach((button) => button.addEventListener("click", () => hideOverlay("#activityOverlay")));
  $("#exportFeedback").addEventListener("click", async () => {
    const result = await api("export_feedback");
    if (result?.ok) showToast(`Feedback exported to ${result.path}`, "success");
  });
  $("#uploadFeedback").addEventListener("click", () => api("upload_feedback"));
  $("#clearFeedback").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    if (button.dataset.armed !== "1") {
      button.dataset.armed = "1";
      button.textContent = "Click again to clear";
      window.setTimeout(() => { button.dataset.armed = ""; button.textContent = "Clear feedback"; }, 3000);
      return;
    }
    await api("clear_feedback");
    button.dataset.armed = ""; button.textContent = "Clear feedback";
    await openActivity();
  });

  $("#setupFinish").addEventListener("click", async () => {
    await api("finish_setup", { gpu_layers: Number($("#setupGpu").value || 99) });
    state.config.first_run_completed = true;
    hideOverlay("#setupOverlay");
  });
  $("#setupSkip").addEventListener("click", async () => {
    await api("finish_setup", { gpu_layers: Number($("#setupGpu").value || 99) });
    state.config.first_run_completed = true;
    hideOverlay("#setupOverlay");
  });

  $("#paletteInput").addEventListener("input", (event) => renderPalette(event.target.value));
  $("#paletteInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("#paletteList .palette-item")?.click();
    }
  });

  $("#promptConfirm").addEventListener("click", () => resolvePrompt($("#promptInput").value));
  $$("[data-close-prompt]").forEach((button) => button.addEventListener("click", () => resolvePrompt(null)));
  $("#confirmationApprove").addEventListener("click", () => resolveActiveConfirmation(true));
  $("#confirmationDeny").addEventListener("click", () => resolveActiveConfirmation(false));

  for (const overlay of $$(".overlay")) {
    overlay.addEventListener("mousedown", (event) => {
      if (event.target !== overlay) return;
      if (overlay.id === "confirmationOverlay") resolveActiveConfirmation(false);
      else if (overlay.id === "promptOverlay") resolvePrompt(null);
      else if (overlay.id !== "setupOverlay") overlay.hidden = true;
    });
  }

  document.addEventListener("keydown", async (event) => {
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if ($("#paletteOverlay").hidden) openPalette(); else hideOverlay("#paletteOverlay");
      return;
    }
    if (modifier && event.key === "Enter") {
      event.preventDefault();
      await regenerate();
      return;
    }
    if (event.key !== "Escape") return;
    if (activeConfirmation) { await resolveActiveConfirmation(false); return; }
    if (!$("#promptOverlay").hidden) { resolvePrompt(null); return; }
    if (!$("#paletteOverlay").hidden) { hideOverlay("#paletteOverlay"); return; }
    if (el.inspector.classList.contains("open")) { el.inspector.classList.remove("open"); return; }
    for (const id of ["#settingsOverlay", "#serverOverlay", "#activityOverlay"]) {
      if (!$(id).hidden) { hideOverlay(id); return; }
    }
    if (state.streaming) await stopGeneration();
  });
}

function applyBootstrap(data = {}) {
  state.app = data.app || {};
  state.config = data.config || {};
  state.credentials = data.credentials || {};
  state.paths = data.paths || {};
  state.conversations = data.conversations || [];
  state.projects = data.projects || [];
  state.skills = data.skills || [];
  state.extensions = data.extensions || state.extensions;
  state.attachments = data.attachments || [];
  state.currentConversationId = String(data.current_conversation_id || "");
  setTheme(state.config.theme || localStorage.getItem("vision.theme") || "dark");
  setEndpointChip(state.config.endpoint_mode === "cloud" ? "cloud" : "local");
  renderProjects();
  renderConversations();
  renderAttachments();
  renderSettings(data);
  if (data.server) renderServer(data.server);
  if (data.chat_busy) setStreaming(true);
}

async function init() {
  registerBridgeEvents();
  bindUi();
  setTheme(localStorage.getItem("vision.theme") || document.documentElement.dataset.theme || "dark", false);
  setSidebarCollapsed(localStorage.getItem("vision.sidebarCollapsed") === "1");
  autoResizeComposer();
  updateComposerMeta();
  updateScrollState();

  state.native = await waitForNative(3500);
  if (!state.native) {
    el.serverPillText.textContent = "Preview mode";
    el.serverDot.className = "status-dot warn";
    showBanner("Preview mode: launch VISION through the desktop shell to use the local model, tools, files, and settings.");
    renderSettings({});
    el.composerInput.focus();
    return;
  }

  const data = await api("get_state");
  if (data) {
    applyBootstrap(data);
    if (state.currentConversationId) {
      const conversation = await api("get_conversation", state.currentConversationId);
      if (conversation) {
        state.currentConversation = conversation.conversation || null;
        renderMessages(conversation.messages || []);
        renderConversations();
      }
    }
  }
  await callOptional("frontend_ready", { ok: true });
  if (state.app.first_run_completed === false || state.config.first_run_completed === false) {
    showOverlay("#setupOverlay");
  }
  el.composerInput.focus();
}

init().catch((error) => {
  console.error(error);
  showBanner(`The web interface could not finish loading: ${error.message || error}`);
});
