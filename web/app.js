const $ = (id) => document.getElementById(id);
const numeric = (value) => typeof value === "number" && Number.isFinite(value);
const array = (value) => (Array.isArray(value) ? value : []);
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const rubles = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});
const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  timeZone: "Europe/Moscow",
});
const fullDateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "Europe/Moscow",
});
const percent = (value) => (numeric(value) ? number.format(value) + "%" : "—");
const money = (value) => (numeric(value) ? rubles.format(value) : "—");
const signedMoney = (value) =>
  numeric(value) && value > 0 ? "+" + money(value) : money(value);
const date = (value, full = false) => {
  const time = numeric(value) ? value : Date.parse(value);
  return Number.isFinite(time)
    ? (full ? fullDateFormat : dateFormat).format(time)
    : "—";
};

const labels = {
  hybrid: "События + рынок",
  market: "Только рынок",
  momentum: "Импульс цены",
};
const palette = { hybrid: "#b9ef91", market: "#98a9ff", momentum: "#e5aa7a" };
const sourceNames = {
  synthetic: "Синтетические данные",
  moex: "Московская биржа",
  csv: "Загруженный архив CSV",
};
const exitReasons = {
  stop: "Стоп",
  take_profit: "Цель",
  close: "Закрытие сессии",
};
const canvas = $("equity-chart");
const context = canvas.getContext("2d");
const wrap = $("chart-wrap");
const tooltip = $("tooltip");
let report;
let strategyKey = "hybrid";
let series = [];
let visible = [];
let geometry;
let selectedTime;
let allTrades = false;
let requestId = 0;

function add(tag, parent, label, className) {
  const element = document.createElement(tag);
  if (label !== undefined) element.textContent = label;
  if (className) element.className = className;
  parent.append(element);
  return element;
}

function setText(id, value, tone = "") {
  const element = $(id);
  element.textContent = value;
  element.classList.remove("good", "bad");
  if (tone) element.classList.add(tone);
}

function notice(message, isError = false) {
  const element = $("notice");
  element.hidden = !message;
  element.textContent = message;
  element.setAttribute("role", isError ? "alert" : "status");
}

function validate(input) {
  if (
    input?.schema_version !== 1 ||
    !input.meta ||
    !input.strategies?.hybrid?.metrics ||
    !Array.isArray(input.strategies.hybrid.equity)
  ) {
    throw new Error(
      "Нужен JSON-отчёт Контур с результатами стратегии и рядом капитала.",
    );
  }
  return input;
}

function strategyMetrics() {
  const metrics = report.strategies[strategyKey].metrics;
  for (const button of $("chart-legend").querySelectorAll("button")) {
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.strategy === strategyKey),
    );
  }
  const net = metrics.net_pnl;
  setText(
    "net-value",
    signedMoney(net),
    net > 0 ? "good" : net < 0 ? "bad" : "",
  );
  setText(
    "net-detail",
    numeric(metrics.return_pct)
      ? percent(metrics.return_pct) + " после издержек"
      : "После торговых издержек",
  );
  setText("drawdown-value", percent(metrics.max_drawdown_pct));
  setText(
    "pf-value",
    numeric(metrics.profit_factor)
      ? number.format(metrics.profit_factor)
      : metrics.no_losses
        ? "∞"
        : "—",
  );
  setText(
    "trades-value",
    numeric(metrics.trade_count) ? number.format(metrics.trade_count) : "—",
  );
  const days = numeric(metrics.trade_days)
    ? "Дней: " + number.format(metrics.trade_days)
    : "Закрытые позиции";
  setText(
    "trades-detail",
    numeric(metrics.expectancy)
      ? days + " · средняя " + signedMoney(metrics.expectancy)
      : days,
  );
  renderTrades();
  renderResearch();
  draw();
}

function update(input) {
  report = validate(input);
  strategyKey = "hybrid";
  allTrades = false;
  const meta = report.meta;
  const badge = $("source-badge");
  badge.hidden = false;
  badge.classList.toggle("demo", meta.dataset !== "moex");
  badge.querySelector("span").textContent =
    sourceNames[meta.dataset] || "Источник отчёта не указан";
  const period =
    meta.oos_start && meta.oos_end
      ? date(meta.oos_start, true) + " — " + date(meta.oos_end, true)
      : "";
  const symbols = array(meta.symbols).filter(
    (symbol) => typeof symbol === "string",
  );
  const start = numeric(meta.start_capital)
    ? "Старт " + money(meta.start_capital)
    : "";
  setText(
    "report-summary",
    [period, symbols.join(" · "), start].filter(Boolean).join("  /  ") ||
      "Загруженный отчёт",
  );
  notice(
    meta.dataset === "synthetic"
      ? "Демо на синтетических данных. Котировки и события сгенерированы для проверки конвейера."
      : "",
  );
  series = Object.keys(labels).flatMap((key) => {
    const item = report.strategies[key];
    if (!item?.metrics) return [];
    const points = array(item.equity)
      .map((point) => ({
        time: Date.parse(point?.date),
        equity: point?.equity,
      }))
      .filter((point) => Number.isFinite(point.time) && numeric(point.equity))
      .sort((left, right) => left.time - right.time);
    return [{ key, name: labels[key], color: palette[key], points }];
  });
  const legend = $("chart-legend");
  legend.replaceChildren();
  for (const item of series) {
    const button = add("button", legend, undefined, "legend-button");
    button.type = "button";
    button.dataset.strategy = item.key;
    add("i", button).style.backgroundColor = item.color;
    button.append(document.createTextNode(item.name));
    button.addEventListener("click", () => {
      strategyKey = item.key;
      allTrades = false;
      strategyMetrics();
    });
  }
  setRange(0);
  strategyMetrics();
}

function safeLink(value) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}

function eventTitle(parent, event) {
  const title = String(event.title || event.symbol || "Событие");
  const url = safeLink(event.url);
  if (url) {
    const link = add("a", parent, title);
    link.href = url;
    link.rel = "noreferrer";
  } else {
    add("span", parent, title);
  }
}

function renderTrades() {
  const strategy = report?.strategies[strategyKey];
  const list = array(strategy?.trades)
    .filter((item) => item && typeof item === "object")
    .slice()
    .reverse();
  const table = $("trade-body");
  const toggle = $("toggle-trades");
  table.replaceChildren();
  toggle.hidden = list.length <= 8;
  toggle.setAttribute("aria-expanded", String(allTrades));
  toggle.textContent = allTrades ? "Свернуть" : "Все сделки";
  setText(
    "trade-summary",
    list.length
      ? labels[strategyKey] + " · " + number.format(list.length) + " ситуаций"
      : "В выбранном прогоне нет закрытых позиций",
  );
  if (!list.length) {
    add(
      "td",
      add("tr", table),
      "Нет сделок по заданным правилам входа.",
      "empty-row",
    ).colSpan = 4;
  }
  for (const trade of allTrades ? list : list.slice(0, 8)) {
    const row = add("tr", table);
    const ticker = add("td", row, undefined, "ticker");
    const details = add("details", ticker);
    add("summary", details, String(trade.symbol ?? "—"));
    add(
      "small",
      details,
      [
        date(trade.signal_date),
        "Прогноз " + percent(trade.forecast_pct),
        number.format(trade.quantity) + " шт.",
      ].join(" · "),
    );
    for (const event of array(trade.evidence).slice(0, 3)) {
      const evidence = add("small", details);
      eventTitle(evidence, event);
      if (event.source) add("span", evidence, " · " + event.source);
    }
    add("td", row, date(trade.entry_at));
    add("td", row, exitReasons[trade.reason] ?? "—");
    add(
      "td",
      row,
      signedMoney(trade.pnl),
      trade.pnl > 0 ? "good" : trade.pnl < 0 ? "bad" : "",
    );
  }
}

function renderResearch() {
  const container = $("research-lines");
  container.replaceChildren();
  const meta = report.meta;
  const metrics = report.strategies[strategyKey].metrics;
  const modelKey = strategyKey === "hybrid" ? "event" : strategyKey;
  const diagnostics = report.diagnostics?.[modelKey] || {};
  const events = array(report.events).filter(
    (item) => item && typeof item === "object",
  );
  const entries = add("dl", container);
  const cost = (value) =>
    numeric(value) ? number.format(value) + " б.п." : "—";
  const rows = [
    [
      "Свечи / события",
      [meta.bars, meta.events]
        .map((value) => (numeric(value) ? number.format(value) : "—"))
        .join(" / "),
    ],
    ["Источник событий", meta.event_source],
    [
      "Комиссия / проскальзывание",
      cost(meta.commission_bps) + " / " + cost(meta.slippage_bps),
    ],
    ["Окна проверки", meta.folds],
    ["Средняя ошибка прогноза", cost(diagnostics.mae_bps)],
    ["Доля результата лучшей сделки", percent(metrics.concentration_pct)],
  ];
  for (const [label, value] of rows) {
    const row = add("div", entries);
    add("dt", row, label);
    add("dd", row, String(value ?? "—"));
  }
  add(
    "p",
    container,
    "Модель обучается на прошлом. Сигнал формируется после закрытия, тестовый вход — на открытии следующей сессии.",
    "note",
  );
  if (metrics.concentration_pct > 60) {
    add(
      "p",
      container,
      "Вклад лучшей сделки превышает 60% чистой прибыли. Для оценки нужна проверка повторяемости.",
      "note",
    );
  }
  if (!events.length) return;
  add("h3", container, "Последние события");
  const list = add("ul", container, undefined, "events");
  for (const event of events.slice(0, 4)) {
    const item = add("li", list);
    eventTitle(item, event);
    add(
      "small",
      item,
      [event.symbol, event.source, event.author, date(event.available_at)]
        .filter(Boolean)
        .join(" · "),
    );
  }
}

function setRange(days) {
  selectedTime = undefined;
  tooltip.hidden = true;
  for (const button of document.querySelectorAll("[data-days]")) {
    button.setAttribute(
      "aria-pressed",
      String(Number(button.dataset.days) === days),
    );
  }
  const nonempty = series.filter((item) => item.points.length);
  const end = Math.max(...nonempty.map((item) => item.points.at(-1).time));
  const start = days ? end - days * 86_400_000 : -Infinity;
  visible = nonempty
    .map((item) => ({
      ...item,
      points: item.points.filter((point) => point.time >= start),
    }))
    .filter((item) => item.points.length);
  draw();
}

function geometryFor(width, height) {
  let lower = Infinity;
  let upper = -Infinity;
  let start = Infinity;
  let end = -Infinity;
  for (const item of visible) {
    start = Math.min(start, item.points[0].time);
    end = Math.max(end, item.points.at(-1).time);
    for (const point of item.points) {
      lower = Math.min(lower, point.equity);
      upper = Math.max(upper, point.equity);
    }
  }
  const pad = Math.max((upper - lower) * 0.13, Math.abs(upper) * 0.005, 1);
  lower -= pad;
  upper += pad;
  const left = 14;
  const right = width - 72;
  const top = 13;
  const bottom = height - 29;
  return {
    start,
    end,
    left,
    right,
    top,
    bottom,
    lower,
    upper,
    x: (time) =>
      start === end
        ? (left + right) / 2
        : left + ((time - start) * (right - left)) / (end - start),
    y: (value) => bottom - ((value - lower) * (bottom - top)) / (upper - lower),
  };
}

function grid(g, width) {
  context.font = "11px system-ui,sans-serif";
  context.strokeStyle = "#34373c";
  context.fillStyle = "#a1a6ad";
  context.textBaseline = "middle";
  context.textAlign = "left";
  for (let index = 0; index <= 4; index++) {
    const value = g.lower + (index * (g.upper - g.lower)) / 4;
    const y = g.y(value);
    context.beginPath();
    context.moveTo(g.left, y);
    context.lineTo(g.right, y);
    context.stroke();
    context.fillText(money(Math.round(value)), g.right + 8, y);
  }
  const ticks = g.start === g.end ? 0 : width > 800 ? 6 : width > 460 ? 4 : 2;
  context.textAlign = "center";
  for (let index = 0; index <= ticks; index++) {
    const time = ticks
      ? g.start + (index * (g.end - g.start)) / ticks
      : g.start;
    context.fillText(date(time), Math.max(28, g.x(time)), g.bottom + 15);
  }
}

function trace(points, g) {
  context.beginPath();
  points.forEach((point, index) => {
    const x = g.x(point.time);
    const y = g.y(point.equity);
    if (index) context.lineTo(x, y);
    else context.moveTo(x, y);
  });
}

function line(item, g) {
  const points = item.points;
  const selected = item.key === strategyKey;
  trace(points, g);
  if (selected && points.length > 1) {
    const gradient = context.createLinearGradient(0, g.top, 0, g.bottom);
    gradient.addColorStop(0, item.color + "30");
    gradient.addColorStop(1, item.color + "00");
    context.lineTo(g.x(points.at(-1).time), g.bottom);
    context.lineTo(g.x(points[0].time), g.bottom);
    context.closePath();
    context.fillStyle = gradient;
    context.fill();
    trace(points, g);
  }
  context.strokeStyle = item.color;
  context.lineWidth = selected ? 2.3 : 1.4;
  context.lineJoin = "round";
  context.stroke();
  if (points.length === 1)
    pointMarker(g.x(points[0].time), g.y(points[0].equity), item.color);
  context.lineWidth = 1;
}

function pointMarker(x, y, color) {
  context.beginPath();
  context.arc(x, y, 3.5, 0, Math.PI * 2);
  context.fillStyle = color;
  context.fill();
}

function nearest(points, time) {
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (points[mid].time < time) low = mid + 1;
    else high = mid;
  }
  return low && time - points[low - 1].time < points[low].time - time
    ? points[low - 1]
    : points[low];
}

function activeSeries() {
  return visible.find((item) => item.key === strategyKey) || visible[0];
}

function showSelection(g, width) {
  if (selectedTime === undefined) return;
  const time = nearest(activeSeries().points, selectedTime).time;
  const x = g.x(time);
  context.save();
  context.strokeStyle = "#898f89";
  context.setLineDash([3, 4]);
  context.beginPath();
  context.moveTo(x, g.top);
  context.lineTo(x, g.bottom);
  context.stroke();
  context.restore();
  tooltip.replaceChildren();
  add("strong", tooltip, date(time, true));
  for (const item of visible) {
    const chosen = nearest(item.points, time);
    const entry = add("div", tooltip);
    add("span", entry, item.name);
    add("b", entry, chosen.time === time ? money(chosen.equity) : "—");
    if (chosen.time === time) pointMarker(x, g.y(chosen.equity), item.color);
  }
  tooltip.hidden = false;
  tooltip.style.left =
    Math.max(5, Math.min(x + 12, width - tooltip.offsetWidth - 5)) + "px";
  tooltip.style.top = g.top + 6 + "px";
}

function draw() {
  const hasPoints = visible.length > 0;
  $("chart-empty").hidden = hasPoints;
  if (!context || !hasPoints) {
    geometry = undefined;
    return;
  }
  const width = wrap.clientWidth;
  const height = wrap.clientHeight;
  if (!width || !height) return;
  const ratio = window.devicePixelRatio || 1;
  const pixelsWide = Math.round(width * ratio);
  const pixelsHigh = Math.round(height * ratio);
  if (canvas.width !== pixelsWide || canvas.height !== pixelsHigh) {
    canvas.width = pixelsWide;
    canvas.height = pixelsHigh;
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  geometry = geometryFor(width, height);
  grid(geometry, width);
  for (const item of visible) if (item !== activeSeries()) line(item, geometry);
  line(activeSeries(), geometry);
  showSelection(geometry, width);
}

function selectPoint(event) {
  if (!geometry) return;
  const x = event.clientX - canvas.getBoundingClientRect().left;
  const fraction = (x - geometry.left) / (geometry.right - geometry.left);
  selectedTime =
    geometry.start +
    Math.max(0, Math.min(1, fraction)) * (geometry.end - geometry.start);
  draw();
}

for (const button of document.querySelectorAll("[data-days]")) {
  button.addEventListener("click", () => setRange(Number(button.dataset.days)));
}
canvas.addEventListener("keydown", (event) => {
  if (
    !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) ||
    !geometry
  )
    return;
  const points = activeSeries().points;
  const current =
    selectedTime === undefined
      ? points.length - 1
      : points.indexOf(nearest(points, selectedTime));
  const index =
    event.key === "Home"
      ? 0
      : event.key === "End"
        ? points.length - 1
        : Math.max(
            0,
            Math.min(
              points.length - 1,
              current + (event.key === "ArrowRight" ? 1 : -1),
            ),
          );
  event.preventDefault();
  selectedTime = points[index].time;
  setText(
    "chart-live",
    date(selectedTime, true) + ", " + money(points[index].equity),
  );
  draw();
});
canvas.addEventListener("pointermove", selectPoint);
canvas.addEventListener("pointerdown", selectPoint);
canvas.addEventListener("pointerleave", () => {
  selectedTime = undefined;
  tooltip.hidden = true;
  draw();
});
$("toggle-trades").addEventListener("click", () => {
  allTrades = !allTrades;
  renderTrades();
});
new ResizeObserver(draw).observe(wrap);

$("report-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = "";
  const id = ++requestId;
  try {
    const input = JSON.parse(await file.text());
    if (id === requestId) update(input);
  } catch (error) {
    if (id === requestId)
      notice(
        error instanceof SyntaxError
          ? "Файл не содержит корректный JSON."
          : error.message,
        true,
      );
  }
});

function activeNav() {
  const anchor = location.hash || "#overview";
  for (const link of document.querySelectorAll("nav a")) {
    if (link.getAttribute("href") === anchor)
      link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}
window.addEventListener("hashchange", activeNav);
activeNav();
renderTrades();

async function initialize() {
  try {
    const embedded = $("embedded-report");
    if (embedded) {
      update(JSON.parse(embedded.textContent));
      return;
    }
    const response = await fetch("/results/report.json");
    if (!response.ok) throw new Error("Отчёт недоступен");
    const input = await response.json();
    if (!requestId) update(input);
  } catch {
    if (requestId) return;
    setText("report-summary", "Откройте сохранённый JSON-отчёт о прогоне.");
    $("source-badge").hidden = true;
    notice(
      "Отчёт по умолчанию недоступен. Загрузите файл через «Открыть отчёт».",
      true,
    );
  }
}
initialize();
