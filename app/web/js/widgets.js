import { Chart, registerables } from "../vendor/chart.js";
import { callOptional, isNative } from "./ipc-bridge.js";

Chart.register(...registerables);

const ACCENT = "#4F8CFF";

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function safeFilename(value, extension) {
  const stem = String(value || "vision-widget")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64) || "vision-widget";
  return `${stem}.${extension}`;
}

function csvCell(value) {
  const text = value === null || value === undefined ? "" : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(rows) {
  return rows.map((row) => row.map(csvCell).join(",")).join("\n") + "\n";
}

async function saveData(filename, data, encoding, mime) {
  if (isNative()) {
    const result = await callOptional("save_export", null, { filename, data, encoding, mime });
    if (result) return result;
  }
  const bytes = encoding === "base64"
    ? Uint8Array.from(atob(data), (char) => char.charCodeAt(0))
    : new TextEncoder().encode(data);
  const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { ok: true };
}

async function saveCanvas(canvas, title) {
  const dataUrl = canvas.toDataURL("image/png");
  return saveData(safeFilename(title, "png"), dataUrl.split(",")[1], "base64", "image/png");
}

async function saveCsv(title, rows) {
  return saveData(safeFilename(title, "csv"), toCsv(rows), "text", "text/csv;charset=utf-8");
}

function createCanvas(width, height) {
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  ctx.fillStyle = cssVar("--surface", "#12151A");
  ctx.fillRect(0, 0, width, height);
  return { canvas, ctx, width, height };
}

function wrapLines(ctx, text, maxWidth, maxLines = 3) {
  const words = String(text || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let line = "";
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word;
    if (ctx.measureText(candidate).width <= maxWidth || !line) {
      line = candidate;
    } else {
      lines.push(line);
      line = word;
      if (lines.length >= maxLines) break;
    }
  }
  if (line && lines.length < maxLines) lines.push(line);
  if (lines.length === maxLines && words.join(" ") !== lines.join(" ")) {
    lines[maxLines - 1] = `${lines[maxLines - 1].replace(/…$/, "")}…`;
  }
  return lines.length ? lines : [""];
}

function drawTextBlock(ctx, text, x, y, maxWidth, lineHeight, maxLines = 3) {
  const lines = wrapLines(ctx, text, maxWidth, maxLines);
  lines.forEach((line, index) => ctx.fillText(line, x, y + index * lineHeight));
  return lines.length * lineHeight;
}

function shell(title, exporters) {
  const widget = document.createElement("section");
  widget.className = "widget";
  const head = document.createElement("div");
  head.className = "widget-head";
  const heading = document.createElement("div");
  heading.className = "widget-title";
  heading.textContent = title || "Result";

  const actions = document.createElement("div");
  actions.className = "widget-actions";
  const menuWrap = document.createElement("div");
  menuWrap.className = "widget-menu";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "icon-button small";
  button.title = "Export widget";
  button.setAttribute("aria-label", "Export widget");
  button.innerHTML = '<svg aria-hidden="true"><use href="#icon-download"></use></svg>';
  const menu = document.createElement("div");
  menu.className = "export-menu";
  menu.hidden = true;

  const pngButton = document.createElement("button");
  pngButton.type = "button";
  pngButton.textContent = "Export PNG";
  pngButton.addEventListener("click", async () => {
    menu.hidden = true;
    await exporters.png();
  });
  const csvButton = document.createElement("button");
  csvButton.type = "button";
  csvButton.textContent = "Export CSV";
  csvButton.addEventListener("click", async () => {
    menu.hidden = true;
    await exporters.csv();
  });
  menu.append(pngButton, csvButton);
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    document.querySelectorAll(".export-menu:not([hidden])").forEach((item) => {
      if (item !== menu) item.hidden = true;
    });
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", () => { menu.hidden = true; });
  menuWrap.append(button, menu);
  actions.append(menuWrap);
  head.append(heading, actions);

  const body = document.createElement("div");
  body.className = "widget-body";
  widget.append(head, body);
  return { widget, body };
}

function renderTable(payload) {
  const columns = Array.isArray(payload.columns) ? payload.columns : [];
  let rows = Array.isArray(payload.rows) ? [...payload.rows] : [];
  let sortIndex = -1;
  let sortDirection = 1;

  const { widget, body } = shell(payload.title || "Table", {
    csv: () => saveCsv(payload.title || "table", [columns, ...rows]),
    png: () => exportTablePng(payload.title || "Table", columns, rows),
  });

  const scroll = document.createElement("div");
  scroll.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "data-table";
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  const tbody = document.createElement("tbody");

  function renderRows() {
    tbody.replaceChildren();
    for (const row of rows) {
      const tr = document.createElement("tr");
      columns.forEach((_column, index) => {
        const td = document.createElement("td");
        td.textContent = row[index] === null || row[index] === undefined ? "" : String(row[index]);
        tr.append(td);
      });
      tbody.append(tr);
    }
    [...headRow.children].forEach((th, index) => {
      const mark = th.querySelector(".sort-mark");
      if (mark) mark.textContent = index === sortIndex ? (sortDirection === 1 ? " ↑" : " ↓") : "";
    });
  }

  columns.forEach((column, index) => {
    const th = document.createElement("th");
    th.textContent = String(column);
    const mark = document.createElement("span");
    mark.className = "sort-mark";
    th.append(mark);
    th.title = "Sort column";
    th.addEventListener("click", () => {
      if (sortIndex === index) sortDirection *= -1;
      else { sortIndex = index; sortDirection = 1; }
      rows.sort((a, b) => {
        const av = a[index];
        const bv = b[index];
        const an = Number(av);
        const bn = Number(bv);
        const result = Number.isFinite(an) && Number.isFinite(bn)
          ? an - bn
          : String(av ?? "").localeCompare(String(bv ?? ""), undefined, { numeric: true });
        return result * sortDirection;
      });
      renderRows();
    });
    headRow.append(th);
  });

  thead.append(headRow);
  table.append(thead, tbody);
  scroll.append(table);
  body.append(scroll);
  renderRows();
  return widget;
}

function exportTablePng(title, columns, rows) {
  const displayRows = rows.slice(0, 100);
  const colWidth = Math.max(120, Math.min(230, Math.floor(1100 / Math.max(columns.length, 1))));
  const width = Math.max(640, columns.length * colWidth + 32);
  const height = 76 + (displayRows.length + 1) * 34;
  const { canvas, ctx } = createCanvas(width, height);
  ctx.fillStyle = cssVar("--text", "#E6E9EF");
  ctx.font = "700 20px Inter, Segoe UI, sans-serif";
  ctx.fillText(title, 16, 30);
  ctx.font = "600 12px Inter, Segoe UI, sans-serif";
  ctx.fillStyle = cssVar("--text-2", "#8A93A3");
  columns.forEach((column, index) => ctx.fillText(String(column).toUpperCase(), 16 + index * colWidth, 62));
  ctx.font = "13px Inter, Segoe UI, sans-serif";
  displayRows.forEach((row, rowIndex) => {
    const y = 94 + rowIndex * 34;
    ctx.strokeStyle = cssVar("--border", "rgba(255,255,255,.07)");
    ctx.beginPath(); ctx.moveTo(16, y - 21); ctx.lineTo(width - 16, y - 21); ctx.stroke();
    ctx.fillStyle = cssVar("--text", "#E6E9EF");
    columns.forEach((_column, index) => {
      const value = String(row[index] ?? "");
      ctx.fillText(value.length > 28 ? `${value.slice(0, 27)}…` : value, 16 + index * colWidth, y);
    });
  });
  return saveCanvas(canvas, title);
}

function renderChart(payload) {
  const labels = Array.isArray(payload.labels) ? payload.labels.map(String) : [];
  const values = Array.isArray(payload.values) ? payload.values.map(Number) : [];
  const type = ["bar", "line", "pie", "scatter"].includes(payload.chart_type) ? payload.chart_type : "bar";
  let chart;

  const { widget, body } = shell(payload.title || "Chart", {
    csv: () => saveCsv(payload.title || "chart", [["label", "value"], ...labels.map((label, i) => [label, values[i]])]),
    png: () => chart ? saveData(safeFilename(payload.title || "chart", "png"), chart.toBase64Image("image/png", 1).split(",")[1], "base64", "image/png") : Promise.resolve(),
  });

  const wrap = document.createElement("div");
  wrap.className = "chart-wrap";
  const canvas = document.createElement("canvas");
  wrap.append(canvas);
  body.append(wrap);

  const secondary = cssVar("--text-2", "#8A93A3");
  const border = cssVar("--border", "rgba(255,255,255,.07)");
  const context = canvas.getContext("2d");
  const gradient = context.createLinearGradient(0, 0, 0, 290);
  gradient.addColorStop(0, "rgba(79,140,255,.88)");
  gradient.addColorStop(1, "rgba(79,140,255,.18)");

  const palette = ["#4F8CFF", "#7AA8FF", "#2F6FE4", "#A9C4FF", "#5B6B82", "#8A93A3", "#3567B8", "#C4D6FF"];
  const data = type === "scatter"
    ? values.map((value, index) => ({ x: Number(labels[index]) || index + 1, y: value }))
    : values;

  chart = new Chart(canvas, {
    type,
    data: {
      labels: type === "scatter" ? undefined : labels,
      datasets: [{
        label: payload.title || "Values",
        data,
        backgroundColor: type === "pie" ? labels.map((_label, i) => palette[i % palette.length]) : gradient,
        borderColor: ACCENT,
        borderWidth: 2,
        borderRadius: type === "bar" ? 7 : 0,
        fill: type === "line",
        tension: type === "line" ? 0.32 : 0,
        pointRadius: type === "scatter" ? 4 : 3,
        pointHoverRadius: 5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 180, easing: "easeOutCubic" },
      plugins: {
        legend: {
          display: type === "pie",
          position: "bottom",
          labels: { color: secondary, boxWidth: 10, boxHeight: 10, usePointStyle: true, font: { family: "Inter, Segoe UI, sans-serif", size: 11 } },
        },
        tooltip: {
          backgroundColor: cssVar("--surface-3", "#1D232C"),
          titleColor: cssVar("--text", "#E6E9EF"),
          bodyColor: secondary,
          borderColor: border,
          borderWidth: 1,
          padding: 10,
        },
      },
      scales: type === "pie" ? {} : {
        x: { grid: { display: false }, border: { color: border }, ticks: { color: secondary, font: { size: 11 } } },
        y: { grid: { color: "rgba(138,147,163,.08)" }, border: { display: false }, ticks: { color: secondary, font: { size: 11 } } },
      },
    },
  });
  return widget;
}

function renderCards(payload) {
  const cards = Array.isArray(payload.cards) ? payload.cards : [];
  const { widget, body } = shell(payload.title || "Cards", {
    csv: () => saveCsv(payload.title || "cards", [["title", "value", "body", "footer"], ...cards.map((c) => [c.title, c.value, c.body, c.footer])]),
    png: () => exportCardsPng(payload.title || "Cards", cards),
  });
  const grid = document.createElement("div");
  grid.className = "cards-grid";
  for (const item of cards) {
    const card = document.createElement("article");
    card.className = "info-card";
    const heading = document.createElement("h4");
    heading.textContent = item.title || "";
    card.append(heading);
    if (item.value) {
      const value = document.createElement("div");
      value.className = "card-value";
      value.textContent = item.value;
      card.append(value);
    }
    if (item.body) {
      const text = document.createElement("p");
      text.textContent = item.body;
      card.append(text);
    }
    if (item.footer) {
      const footer = document.createElement("footer");
      footer.textContent = item.footer;
      card.append(footer);
    }
    grid.append(card);
  }
  body.append(grid);
  return widget;
}

function exportCardsPng(title, cards) {
  const columns = Math.min(3, Math.max(1, cards.length));
  const cardW = 250;
  const cardH = 150;
  const rows = Math.ceil(cards.length / columns);
  const width = columns * cardW + (columns + 1) * 16;
  const height = 64 + rows * (cardH + 16);
  const { canvas, ctx } = createCanvas(width, height);
  ctx.fillStyle = cssVar("--text", "#E6E9EF");
  ctx.font = "700 20px Inter, Segoe UI, sans-serif";
  ctx.fillText(title, 16, 32);
  cards.forEach((item, index) => {
    const x = 16 + (index % columns) * (cardW + 16);
    const y = 52 + Math.floor(index / columns) * (cardH + 16);
    ctx.fillStyle = cssVar("--surface-2", "#171B21");
    ctx.beginPath(); ctx.roundRect(x, y, cardW, cardH, 12); ctx.fill();
    ctx.strokeStyle = cssVar("--border", "rgba(255,255,255,.07)"); ctx.stroke();
    ctx.fillStyle = cssVar("--text-2", "#8A93A3");
    ctx.font = "600 11px Inter, Segoe UI, sans-serif";
    ctx.fillText(String(item.title || "").toUpperCase(), x + 14, y + 24);
    ctx.fillStyle = cssVar("--text", "#E6E9EF");
    ctx.font = "700 24px Inter, Segoe UI, sans-serif";
    ctx.fillText(String(item.value || ""), x + 14, y + 58);
    ctx.font = "13px Inter, Segoe UI, sans-serif";
    ctx.fillStyle = cssVar("--text-2", "#8A93A3");
    drawTextBlock(ctx, item.body || item.footer || "", x + 14, y + 84, cardW - 28, 18, 3);
  });
  return saveCanvas(canvas, title);
}

function renderTimeline(payload) {
  const events = Array.isArray(payload.events) ? payload.events : [];
  const { widget, body } = shell(payload.title || "Timeline", {
    csv: () => saveCsv(payload.title || "timeline", [["time", "title", "description"], ...events.map((e) => [e.time, e.title, e.description])]),
    png: () => exportTimelinePng(payload.title || "Timeline", events),
  });
  const list = document.createElement("div");
  list.className = "timeline";
  for (const event of events) {
    const item = document.createElement("article");
    item.className = "timeline-item";
    const time = document.createElement("div");
    time.className = "timeline-time";
    time.textContent = event.time || "";
    const title = document.createElement("h4");
    title.textContent = event.title || "";
    item.append(time, title);
    if (event.description) {
      const text = document.createElement("p");
      text.textContent = event.description;
      item.append(text);
    }
    list.append(item);
  }
  body.append(list);
  return widget;
}

function exportTimelinePng(title, events) {
  const width = 860;
  const height = 76 + events.slice(0, 50).length * 82;
  const { canvas, ctx } = createCanvas(width, height);
  ctx.fillStyle = cssVar("--text", "#E6E9EF");
  ctx.font = "700 20px Inter, Segoe UI, sans-serif";
  ctx.fillText(title, 20, 32);
  ctx.strokeStyle = cssVar("--border-strong", "rgba(255,255,255,.12)");
  ctx.beginPath(); ctx.moveTo(34, 56); ctx.lineTo(34, height - 20); ctx.stroke();
  events.slice(0, 50).forEach((event, index) => {
    const y = 76 + index * 82;
    ctx.fillStyle = ACCENT;
    ctx.beginPath(); ctx.arc(34, y - 5, 5, 0, Math.PI * 2); ctx.fill();
    ctx.font = "700 11px Inter, Segoe UI, sans-serif";
    ctx.fillText(String(event.time || ""), 54, y - 8);
    ctx.fillStyle = cssVar("--text", "#E6E9EF");
    ctx.font = "700 15px Inter, Segoe UI, sans-serif";
    ctx.fillText(String(event.title || ""), 54, y + 13);
    ctx.fillStyle = cssVar("--text-2", "#8A93A3");
    ctx.font = "13px Inter, Segoe UI, sans-serif";
    drawTextBlock(ctx, event.description || "", 54, y + 34, width - 80, 17, 2);
  });
  return saveCanvas(canvas, title);
}

function diffRows(beforeLines, afterLines) {
  let start = 0;
  while (start < beforeLines.length && start < afterLines.length && beforeLines[start] === afterLines[start]) start += 1;
  let beforeEnd = beforeLines.length;
  let afterEnd = afterLines.length;
  while (beforeEnd > start && afterEnd > start && beforeLines[beforeEnd - 1] === afterLines[afterEnd - 1]) {
    beforeEnd -= 1;
    afterEnd -= 1;
  }
  const rows = [];
  for (let i = 0; i < start; i += 1) rows.push({ status: "same", beforeNo: i + 1, afterNo: i + 1, before: beforeLines[i], after: afterLines[i] });
  const middleBefore = beforeLines.slice(start, beforeEnd);
  const middleAfter = afterLines.slice(start, afterEnd);
  const max = Math.max(middleBefore.length, middleAfter.length);
  for (let i = 0; i < max; i += 1) {
    rows.push({
      status: middleBefore[i] !== undefined && middleAfter[i] !== undefined ? "changed" : (middleBefore[i] !== undefined ? "removed" : "added"),
      beforeNo: middleBefore[i] !== undefined ? start + i + 1 : "",
      afterNo: middleAfter[i] !== undefined ? start + i + 1 : "",
      before: middleBefore[i] ?? "",
      after: middleAfter[i] ?? "",
    });
  }
  for (let i = beforeEnd, j = afterEnd; i < beforeLines.length && j < afterLines.length; i += 1, j += 1) {
    rows.push({ status: "same", beforeNo: i + 1, afterNo: j + 1, before: beforeLines[i], after: afterLines[j] });
  }
  return rows;
}

function renderDiff(payload) {
  const beforeLines = String(payload.before || "").split("\n").slice(0, 1200);
  const afterLines = String(payload.after || "").split("\n").slice(0, 1200);
  const rows = diffRows(beforeLines, afterLines);
  const title = payload.filename || payload.title || "Diff";
  const { widget, body } = shell(title, {
    csv: () => saveCsv(title, [["status", "before_line", "before", "after_line", "after"], ...rows.map((r) => [r.status, r.beforeNo, r.before, r.afterNo, r.after])]),
    png: () => exportDiffPng(title, rows),
  });

  const wrap = document.createElement("div");
  wrap.className = "diff-wrap";
  const table = document.createElement("table");
  table.className = "diff-table";
  table.innerHTML = "<thead><tr><th></th><th>Before</th><th></th><th>After</th></tr></thead>";
  const tbody = document.createElement("tbody");
  for (const row of rows.slice(0, 500)) {
    const tr = document.createElement("tr");
    const beforeNo = document.createElement("td"); beforeNo.className = "line-no"; beforeNo.textContent = row.beforeNo;
    const before = document.createElement("td"); before.textContent = row.before;
    if (row.status !== "same" && row.before) before.className = "removed";
    const afterNo = document.createElement("td"); afterNo.className = "line-no"; afterNo.textContent = row.afterNo;
    const after = document.createElement("td"); after.textContent = row.after;
    if (row.status !== "same" && row.after) after.className = "added";
    tr.append(beforeNo, before, afterNo, after);
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  body.append(wrap);
  if (rows.length > 500) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = `Showing the first 500 of ${rows.length} diff rows. CSV export includes the rendered comparison rows.`;
    body.append(note);
  }
  return widget;
}

function exportDiffPng(title, rows) {
  const shown = rows.slice(0, 120);
  const width = 1120;
  const height = 72 + shown.length * 24;
  const { canvas, ctx } = createCanvas(width, height);
  ctx.fillStyle = cssVar("--text", "#E6E9EF");
  ctx.font = "700 19px Inter, Segoe UI, sans-serif";
  ctx.fillText(title, 16, 30);
  ctx.font = "12px JetBrains Mono, Consolas, monospace";
  shown.forEach((row, index) => {
    const y = 58 + index * 24;
    if (row.status !== "same") {
      ctx.fillStyle = row.before ? "rgba(255,107,107,.09)" : "rgba(53,196,141,.09)";
      ctx.fillRect(12, y - 15, width - 24, 21);
    }
    ctx.fillStyle = cssVar("--text-3", "#66707F");
    ctx.fillText(String(row.beforeNo).padStart(4, " "), 16, y);
    ctx.fillText(String(row.afterNo).padStart(4, " "), 566, y);
    ctx.fillStyle = cssVar("--text", "#E6E9EF");
    ctx.fillText(String(row.before).slice(0, 72), 70, y);
    ctx.fillText(String(row.after).slice(0, 72), 620, y);
  });
  return saveCanvas(canvas, title);
}

export function renderGenUI(payload = {}) {
  switch (payload.kind) {
    case "table": return renderTable(payload);
    case "chart": return renderChart(payload);
    case "cards": return renderCards(payload);
    case "timeline": return renderTimeline(payload);
    case "diff": return renderDiff(payload);
    default: {
      const { widget, body } = shell(payload.title || "Result", {
        csv: () => saveCsv(payload.title || "result", [["value"], [JSON.stringify(payload)]]),
        png: () => {
          const { canvas, ctx } = createCanvas(760, 220);
          ctx.fillStyle = cssVar("--text", "#E6E9EF");
          ctx.font = "700 20px Inter, Segoe UI, sans-serif";
          ctx.fillText(payload.title || "Result", 20, 38);
          ctx.font = "13px Inter, Segoe UI, sans-serif";
          ctx.fillStyle = cssVar("--text-2", "#8A93A3");
          drawTextBlock(ctx, JSON.stringify(payload), 20, 72, 720, 19, 6);
          return saveCanvas(canvas, payload.title || "result");
        },
      });
      const pre = document.createElement("pre");
      pre.className = "json-block";
      pre.textContent = JSON.stringify(payload, null, 2);
      body.append(pre);
      return widget;
    }
  }
}
