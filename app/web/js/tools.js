import { expandElement } from "./motion.js";
import { renderGenUI } from "./widgets.js";

const CATEGORY_BY_TOOL = new Map([
  ["web_search", "data"], ["get_news", "data"], ["get_weather", "data"],
  ["get_stock_price", "data"], ["get_crypto_price", "data"], ["lookup_docs", "data"], ["read_url", "data"],
  ["run_python", "code"], ["search_code", "code"],
  ["read_file", "files"], ["write_file", "files"], ["list_files", "files"],
  ["memory_get", "memory"], ["memory_set", "memory"], ["skill_load", "memory"],
  ["ui_table", "ui"], ["ui_chart", "ui"], ["ui_cards", "ui"], ["ui_timeline", "ui"], ["ui_code_diff", "ui"],
  ["list_running_apps", "windows"], ["open_app", "windows"], ["focus_window", "windows"],
  ["open_path", "windows"], ["open_url", "windows"], ["clipboard_get", "windows"],
  ["clipboard_set", "windows"], ["system_status", "windows"], ["set_timer", "windows"],
]);

const ICON_BY_CATEGORY = {
  data: "icon-database",
  code: "icon-code",
  files: "icon-folder",
  memory: "icon-bookmark",
  ui: "icon-layout",
  windows: "icon-window",
  extension: "icon-puzzle",
};

export function categoryForTool(name) {
  return CATEGORY_BY_TOOL.get(name) || "extension";
}

export function iconSvg(iconId, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${iconId}`);
  svg.append(use);
  return svg;
}

export function parseArguments(raw) {
  if (raw && typeof raw === "object") return raw;
  if (typeof raw !== "string" || !raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch (_error) {
    return raw;
  }
}

export function pretty(value) {
  if (typeof value === "string") {
    const parsed = parseArguments(value);
    return typeof parsed === "string" ? parsed : JSON.stringify(parsed, null, 2);
  }
  return JSON.stringify(value ?? {}, null, 2);
}

export function oneLine(value, limit = 150) {
  const parsed = parseArguments(value);
  const text = typeof parsed === "string" ? parsed : JSON.stringify(parsed);
  const clean = String(text || "{}").replace(/\s+/g, " ");
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
}

function formatDuration(ms) {
  if (!Number.isFinite(ms)) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

function detailBlock(label, value) {
  const wrapper = document.createElement("div");
  const heading = document.createElement("div");
  heading.className = "detail-label";
  heading.textContent = label;
  const pre = document.createElement("pre");
  pre.className = "json-block";
  pre.textContent = pretty(value);
  wrapper.append(heading, pre);
  return wrapper;
}

export function createToolCard({ id = "", name = "tool", arguments: rawArgs = {} } = {}) {
  const category = categoryForTool(name);
  const card = document.createElement("article");
  card.className = "tool-card running";
  card.dataset.toolId = String(id || "");
  card.dataset.toolName = name;
  card._startedAt = performance.now();
  card._arguments = parseArguments(rawArgs);

  const header = document.createElement("button");
  header.type = "button";
  header.className = "tool-head";
  header.setAttribute("aria-expanded", "false");

  const icon = document.createElement("span");
  icon.className = "tool-icon";
  icon.title = category;
  icon.append(iconSvg(ICON_BY_CATEGORY[category] || ICON_BY_CATEGORY.extension));

  const main = document.createElement("span");
  main.className = "tool-main";
  const nameRow = document.createElement("span");
  nameRow.className = "tool-name-row";
  const title = document.createElement("span");
  title.className = "tool-name";
  title.textContent = name;
  const duration = document.createElement("span");
  duration.className = "tool-duration";
  duration.textContent = "running";
  nameRow.append(title, duration);
  const preview = document.createElement("span");
  preview.className = "tool-args-preview";
  preview.textContent = oneLine(card._arguments);
  main.append(nameRow, preview);

  const status = document.createElement("span");
  status.className = "tool-status";
  status.innerHTML = '<span class="spinner" aria-label="Running"></span>';

  header.append(icon, main, status);

  const widgetSlot = document.createElement("div");
  widgetSlot.className = "tool-widget-slot";

  const details = document.createElement("div");
  details.className = "tool-details";
  const summary = document.createElement("p");
  summary.className = "tool-summary";
  summary.textContent = "Running…";
  details.append(summary, detailBlock("Arguments", card._arguments));

  header.addEventListener("click", () => {
    const open = !card.classList.contains("open");
    card.classList.toggle("open", open);
    header.setAttribute("aria-expanded", String(open));
    expandElement(details, open);
  });

  card.append(header, widgetSlot, details);
  card._durationEl = duration;
  card._statusEl = status;
  card._summaryEl = summary;
  card._detailsEl = details;
  card._headerEl = header;
  card._widgetSlot = widgetSlot;
  card._timer = window.setInterval(() => {
    duration.textContent = formatDuration(performance.now() - card._startedAt);
  }, 250);
  return card;
}

export function finishToolCard(card, result = {}) {
  if (!card) return;
  window.clearInterval(card._timer);
  const failed = Boolean(result.is_error || result.error_kind);
  card.classList.remove("running");
  card.classList.add(failed ? "error" : "success");
  card._statusEl.replaceChildren(iconSvg(failed ? "icon-x" : "icon-check"));
  card._durationEl.textContent = formatDuration(Number(result.duration_ms) || (performance.now() - card._startedAt));
  card._summaryEl.textContent = result.summary || (failed ? "Tool failed" : "Completed");

  const resultValue = result.raw ?? result.content ?? "";
  card._detailsEl.append(detailBlock(failed ? "Error / result" : "Result", resultValue));
  if (result.genui) {
    card._widgetSlot.replaceChildren(renderGenUI(result.genui));
  }
}
