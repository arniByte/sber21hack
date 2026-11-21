const $ = (id) => document.getElementById(id);
const finite = (value) => typeof value === "number" && Number.isFinite(value);
const record = (value) => value && typeof value === "object" && !Array.isArray(value);
const rows = (value) => Array.isArray(value) ? value.filter(record) : [];
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const currency = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "Europe/Moscow" });
const fullDateFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Moscow" });
const timeFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
const num = (value) => finite(value) ? number.format(value) : "—";
const pct = (value) => finite(value) ? num(value) + "%" : "—";
const rub = (value) => finite(value) ? currency.format(value) : "—";
const signedRub = (value) => finite(value) && value > 0 ? "+" + rub(value) : rub(value);
const bps = (value) => finite(value) ? num(value) + " б.п." : "—";
const dayOf = (trade) => trade.trade_day || (typeof trade.entry_at === "string" ? trade.entry_at.slice(0, 10) : "");
const tone = (value) => value > 0 ? "good" : value < 0 ? "bad" : "";
const strategies = {
  hybrid: { label: "События + рынок", color: "#6950ca", hue: 0 },
  market: { label: "Только рынок", color: "#337d8c", hue: 1.7 },
  momentum: { label: "Импульс цены", color: "#ab632f", hue: 3.3 },
};
const sources = { synthetic: "Синтетические данные", moex: "Котировки MOEX", csv: "Импорт CSV" };
const exitReasons = { stop: "Стоп", take_profit: "Цель", close: "Закрытие" };
const topics = {
  comparison: "Как различаются стратегии",
  risk: "Что означает просадка",
  trade: "Как устроена сделка",
  data: "На чём проверяли",
};
const topicsByWords = [
  ["risk", /просад|риск|убыт|profit.?factor|профит.?фактор|лимит/iu],
  ["comparison", /сравн|разниц|какая.*лучш/iu],
  ["trade", /сделк|торгов|сигнал|прогноз|вход|вош[её]л|закрыт|комисс|издерж/iu],
  ["data", /данн|обуч|источ|синтет|архив|моекс|бирж|новос|cassandra/iu],
  ["comparison", /стратег|доход|прибыл|результат|капитал/iu],
];
const motion = matchMedia("(prefers-reduced-motion: reduce)");

let report;
let strategyKey = "hybrid";
let metricKey = "equity";
let rangeDays = 0;
let filterDay = "";
let showAll = false;
let topic;
let loadId = 0;

const chart = new window.KonturChart($("equity-chart"), $("tooltip"), $("chart-live"), selectTradeDay);

function dateLabel(value, formatter = dateFormat) {
  const time = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? formatter.format(time) : "—";
}

function add(tag, target, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  target.append(element);
  return element;
}

function setText(id, value, color) {
  const element = $(id);
  element.textContent = value;
  element.classList.toggle("good", color === "good");
  element.classList.toggle("bad", color === "bad");
}

function showNotice(message) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
}

function validate(input) {
  if (input?.schema_version !== 1 || !record(input.meta) || !record(input.strategies)) {
    throw new Error("Выберите JSON-отчёт Контур версии 1.");
  }
  const hybrid = input.strategies.hybrid;
  if (!record(hybrid?.metrics) || !Array.isArray(hybrid.equity) || !Array.isArray(hybrid.trades)) {
    throw new Error("В отчёте нет метрик, капитала или журнала событийной стратегии.");
  }
  return input;
}

function strategy() { return report?.strategies[strategyKey]; }
function activeTrades() { return rows(strategy()?.trades); }

function setReport(input) {
  report = validate(input);
  strategyKey = "hybrid";
  metricKey = "equity";
  rangeDays = 0;
  filterDay = "";
  showAll = false;
  showNotice("");
  closeAnswer(false);

  const meta = report.meta;
  const badge = $("source-badge");
  badge.hidden = false;
  badge.classList.toggle("demo", meta.dataset === "synthetic");
  badge.querySelector("span").textContent = sources[meta.dataset] || "Источник не указан";
  const dateRange = meta.oos_start && meta.oos_end
    ? dateLabel(meta.oos_start, fullDateFormat) + " — " + dateLabel(meta.oos_end, fullDateFormat)
    : "Период не указан";
  setText("report-summary", dateRange);
  const heroMeta = $("hero-meta");
  heroMeta.replaceChildren();
  if (finite(meta.start_capital)) add("span", heroMeta, "Старт " + rub(meta.start_capital));
  const symbols = Array.isArray(meta.symbols) ? meta.symbols.filter((item) => typeof item === "string") : [];
  for (const symbol of symbols.slice(0, 6)) add("span", heroMeta, symbol);
  if (symbols.length > 6) add("span", heroMeta, "+" + (symbols.length - 6));

  const legend = $("chart-legend");
  legend.replaceChildren();
  for (const [key, config] of Object.entries(strategies)) {
    if (!record(report.strategies[key]?.metrics)) continue;
    const button = add("button", legend);
    button.type = "button";
    button.dataset.strategy = key;
    add("i", button).style.backgroundColor = config.color;
    button.append(document.createTextNode(config.label));
    button.addEventListener("click", () => {
      if (strategyKey === key) return;
      strategyKey = key;
      filterDay = "";
      showAll = false;
      renderStrategy();
    });
  }
  renderStrategy();
}

function renderStrategy() {
  const metrics = strategy().metrics;
  setText("net-value", signedRub(metrics.net_pnl), tone(metrics.net_pnl));
  setText("net-detail", finite(metrics.return_pct) ? pct(metrics.return_pct) + " после издержек" : "С учётом издержек");
  setText("drawdown-value", pct(metrics.max_drawdown_pct));
  setText("pf-value", finite(metrics.profit_factor) ? num(metrics.profit_factor) : metrics.no_losses ? "∞" : "—");
  setText("trades-value", num(metrics.trade_count));
  setText("trades-detail", finite(metrics.trade_days) ? "Торговых дней: " + num(metrics.trade_days) : "Закрытые позиции");
  for (const button of $("chart-legend").querySelectorAll("button")) {
    button.setAttribute("aria-pressed", String(button.dataset.strategy === strategyKey));
  }
  renderChart();
  renderTrades();
  renderResearch();
  if (topic) showAnswer(topic);
  redrawPrism(true);
}

function renderChart() {
  if (!report) return;
  for (const button of document.querySelectorAll("[data-metric]")) {
    button.setAttribute("aria-pressed", String(button.dataset.metric === metricKey));
  }
  for (const button of document.querySelectorAll("[data-days]")) {
    button.setAttribute("aria-pressed", String(Number(button.dataset.days) === rangeDays));
  }
  chart.update({
    strategies: Object.entries(strategies).flatMap(([key, config]) => (
      report.strategies[key] ? [{ key, ...config, equity: rows(report.strategies[key].equity) }] : []
    )),
    selected: strategyKey,
    metric: metricKey,
    days: rangeDays,
    trades: activeTrades(),
    firstCapital: report.meta.start_capital,
  });
}

function eventTitle(target, event) {
  const text = String(event.title || event.symbol || "Событие");
  let link;
  try {
    const url = new URL(event.url);
    if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password) link = url.href;
  } catch { /* A report can omit the source URL. */ }
  if (link) {
    const anchor = add("a", target, text);
    anchor.href = link;
    anchor.rel = "noreferrer";
  } else {
    add("span", target, text);
  }
}

function selectTradeDay(day) {
  if (!report) return;
  filterDay = day;
  showAll = false;
  renderTrades();
  location.hash = "trades";
  $("trades-title").tabIndex = -1;
  $("trades-title").focus({ preventScroll: true });
  $("trades").scrollIntoView({ behavior: motion.matches ? "auto" : "smooth", block: "start" });
}

function selectedTrades() {
  const trades = activeTrades();
  return filterDay ? trades.filter((trade) => dayOf(trade) === filterDay) : trades;
}

function renderTrades() {
  const list = selectedTrades().slice().sort((a, b) => String(b.entry_at).localeCompare(String(a.entry_at)));
  $("date-filter").hidden = !filterDay;
  setText("filter-label", dateLabel(filterDay, fullDateFormat));
  $("export-trades").disabled = !list.length;
  const toggle = $("toggle-trades");
  toggle.hidden = list.length <= 8;
  toggle.setAttribute("aria-expanded", String(showAll));
  toggle.textContent = showAll ? "Свернуть журнал" : "Показать все сделки";
  setText("trade-summary", strategy() ? strategies[strategyKey].label + " · " + num(list.length) + " в выборке" : "Сделки по выбранной стратегии");
  const table = $("trade-body");
  table.replaceChildren();
  if (!list.length) {
    const text = filterDay ? "В выбранную дату сделок не было." : "Нет сделок по заданным правилам входа.";
    add("td", add("tr", table), text, "empty-row").colSpan = 4;
  }
  for (const trade of showAll ? list : list.slice(0, 8)) {
    const row = add("tr", table);
    const details = add("details", add("td", row, undefined, "ticker"));
    add("summary", details, String(trade.symbol ?? "—"));
    add("small", details, "Сигнал " + dateLabel(trade.signal_date) + " · прогноз " + pct(trade.forecast_pct));
    add("small", details, num(trade.quantity) + " шт. · " + rub(trade.entry_price) + " → " + rub(trade.exit_price));
    add("small", details, "Комиссия " + rub(trade.commission) + " · проскальзывание " + rub(trade.slippage));
    for (const event of rows(trade.evidence).slice(0, 3)) {
      const evidence = add("small", details);
      eventTitle(evidence, event);
      if (event.source) add("span", evidence, " · " + event.source);
    }
    add("td", row, dateLabel(trade.entry_at));
    add("td", row, exitReasons[trade.reason] ?? "—");
    add("td", row, signedRub(trade.pnl), tone(trade.pnl));
  }
}

function renderResearch() {
  const container = $("research-lines");
  container.replaceChildren();
  const meta = report.meta;
  const metrics = strategy().metrics;
  const modelKey = strategyKey === "hybrid" ? "event" : strategyKey;
  const diagnostics = report.diagnostics?.[modelKey] || {};
  const events = rows(report.events);
  const entries = add("dl", container);
  const values = [
    ["Свечи / события", num(meta.bars) + " / " + num(meta.events)],
    ["Источник событий", meta.event_source],
    ["Окна проверки", num(meta.folds)],
    ["Средняя ошибка прогноза", bps(diagnostics.mae_bps)],
    ["Комиссия / проскальзывание", bps(meta.commission_bps) + " / " + bps(meta.slippage_bps)],
    ["Доля лучшей сделки", pct(metrics.concentration_pct)],
  ];
  for (const [label, value] of values) {
    const entry = add("div", entries);
    add("dt", entry, label);
    add("dd", entry, String(value ?? "—"));
  }
  if (meta.dataset === "synthetic") {
    add("p", container, "В демонстрационном сценарии цены и новости сгенерированы, а влияние событий задано заранее.");
  }
  if (metrics.concentration_pct > 60) {
    add("p", container, "Больше 60% чистой прибыли пришлось на одну ситуацию.");
  }
  if (events.length) {
    add("h3", container, "Последние события в архиве");
    const list = add("ul", container, undefined, "events");
    for (const event of events.slice(0, 3)) {
      const item = add("li", list);
      eventTitle(item, event);
      add("small", item, [event.symbol, event.source, "доступно " + dateLabel(event.available_at, timeFormat)].filter(Boolean).join(" · "));
    }
  }
}

function explain(container, key) {
  if (!report) {
    add("p", container, "Откройте JSON-отчёт, чтобы разобрать результаты.");
    return;
  }
  const meta = report.meta;
  const selected = strategy();
  const { metrics } = selected;
  const risk = record(selected.risk) ? selected.risk : {};
  const label = strategies[strategyKey].label;
  if (key === "comparison") {
    const list = add("ul", container);
    for (const [id, config] of Object.entries(strategies)) {
      const item = report.strategies[id]?.metrics;
      if (!item) continue;
      add("li", list, `${config.label} · ${signedRub(item.net_pnl)} · ${num(item.trade_count)} ситуаций · просадка ${pct(item.max_drawdown_pct)}`);
    }
    if (meta.dataset === "synthetic") add("p", container, "Разница получена в демонстрационном сценарии с заданным эффектом новостей.");
    return;
  }
  if (key === "risk") {
    add("p", container, `В стратегии «${label}» максимальная просадка составила ${pct(metrics.max_drawdown_pct)}. Это наибольшее падение капитала от предыдущего пика с учётом моделируемых потерь внутри дня.`);
    if (finite(risk.max_daily_loss_pct)) {
      add("p", container, `Бюджет риска на день — ${pct(risk.max_daily_loss_pct)}. После просадки ${pct(risk.max_drawdown_pct)} и выше симулятор приостанавливает новые входы.`);
    }
    if (finite(metrics.profit_factor)) add("p", container, `Profit Factor ${num(metrics.profit_factor)} — отношение суммы прибыльных сделок к сумме убытков. Обе суммы после издержек.`);
    return;
  }
  if (key === "trade") {
    if (finite(risk.min_signal_bps)) {
      add("p", container, `После закрытия модель оценивает доходность от открытия до закрытия следующей сессии. Для входа прогноз должен превышать ${bps(risk.min_signal_bps)} и ожидаемые издержки.`);
      add("p", container, `Вход моделируется на открытии. Выход — по стопу ${pct(risk.stop_pct)}, цели ${pct(risk.take_profit_pct)} или в конце сессии.`);
    } else {
      add("p", container, "Откройте строку сделки, чтобы посмотреть прогноз, размер позиции, цены и издержки.");
    }
    add("a", container, "Посмотреть журнал", "answer-link").href = "#trades";
    return;
  }
  if (key === "data") {
    add("p", container, `${sources[meta.dataset] || "Данные из отчёта"}. В архиве ${num(meta.bars)} дневных свечей и ${num(meta.events)} событий. Прогнозы проверялись в ${num(meta.folds)} последовательных окнах: обучение использовало только уже наблюдаемую историю.`);
    if (meta.dataset === "synthetic") add("p", container, "Синтетический сценарий проверяет конвейер. Для оценки на рынке нужны исторические котировки и даты, когда новости стали доступны стратегии.");
    add("a", container, "Источники и методика", "answer-link").href = "#research";
    return;
  }
  add("p", container, "Введите вопрос про стратегии, риск, сделку или данные. Эти темы разбираются по цифрам текущего отчёта.");
}

function showAnswer(key) {
  topic = key;
  const answer = $("answer");
  const body = $("answer-content");
  body.replaceChildren();
  setText("answer-title", topics[key] || "Что можно разобрать");
  answer.hidden = false;
  explain(body, key);
}

function closeAnswer(focus = true) {
  topic = undefined;
  $("answer").hidden = true;
  if (focus) $("question").focus();
}

function csvCell(value) {
  const text = String(value ?? "");
  const safeText = typeof value === "string" && /^\s*[=+@\-]/u.test(text) ? "'" + text : text;
  return '"' + safeText.replaceAll('"', '""') + '"';
}

function exportTrades() {
  const fields = ["id", "symbol", "signal_at", "entry_at", "exit_at", "trade_day", "quantity", "entry_price", "exit_price", "commission", "slippage", "pnl", "reason"];
  const csv = [fields.join(","), ...selectedTrades().map((trade) => fields.map((field) => csvCell(trade[field])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `kontur-${strategyKey}${filterDay ? "-" + filterDay : ""}.csv`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function activeNav() {
  const current = location.hash || "#overview";
  for (const link of document.querySelectorAll(".navigation a")) {
    if (link.getAttribute("href") === current) link.setAttribute("aria-current", "location");
    else link.removeAttribute("aria-current");
  }
}

for (const button of document.querySelectorAll("[data-metric]")) {
  button.addEventListener("click", () => { metricKey = button.dataset.metric; renderChart(); });
}
for (const button of document.querySelectorAll("[data-days]")) {
  button.addEventListener("click", () => { rangeDays = Number(button.dataset.days); renderChart(); });
}
for (const button of document.querySelectorAll("[data-topic]")) {
  button.addEventListener("click", () => showAnswer(button.dataset.topic));
}
$("question-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const query = $("question").value.trim();
  const matched = topicsByWords.find(([, words]) => words.test(query));
  showAnswer(matched?.[0] || "help");
});
$("clear-answer").addEventListener("click", () => closeAnswer());
$("open-report").addEventListener("click", () => $("report-file").click());
$("toggle-trades").addEventListener("click", () => { showAll = !showAll; renderTrades(); });
$("clear-filter").addEventListener("click", () => { filterDay = ""; renderTrades(); $("trades-title").focus(); });
$("export-trades").addEventListener("click", exportTrades);
window.addEventListener("hashchange", activeNav);
activeNav();
renderTrades();

$("report-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = "";
  const id = ++loadId;
  try {
    const text = await file.text();
    if (id === loadId) setReport(JSON.parse(text));
  } catch (error) {
    if (id === loadId) showNotice(error instanceof SyntaxError ? "Файл не содержит корректный JSON." : error.message);
  }
});

async function initialize() {
  try {
    const embedded = $("embedded-report");
    if (embedded) {
      setReport(JSON.parse(embedded.textContent));
      return;
    }
    const response = await fetch("/results/report.json");
    if (!response.ok) throw new Error("Отчёт недоступен");
    const input = await response.json();
    if (!loadId) setReport(input);
  } catch {
    if (loadId) return;
    setText("report-summary", "Откройте сохранённый JSON-отчёт.");
    showNotice("Отчёт по умолчанию недоступен. Выберите файл через «Открыть JSON».");
  }
}

// The prism uses the active series to vary its contour. Numeric values are on the chart.
const prism = $("prism");
const optics = prism.getContext("2d");
function redrawPrism(animate = false) {
  if (!optics || !prism.clientWidth || !prism.clientHeight) return;
  const width = prism.clientWidth;
  const height = prism.clientHeight;
  const ratio = Math.min(devicePixelRatio || 1, 1.4);
  prism.width = Math.round(width * ratio);
  prism.height = Math.round(height * ratio);
  optics.scale(ratio, ratio);
  const x = width * 0.65;
  const y = height * 0.49;
  const radius = Math.min(height * 0.55, width * 0.47);
  const hue = strategies[strategyKey].hue;
  const series = rows(strategy()?.equity).filter((point) => finite(point.equity)).slice(-180);
  const low = series.reduce((value, point) => Math.min(value, point.equity), Infinity);
  const high = series.reduce((value, point) => Math.max(value, point.equity), -Infinity);
  const samples = series.map((point) => (point.equity - low) / Math.max(1, high - low) - 0.5);

  const mist = optics.createRadialGradient(x, y, 0, x, y, radius * 1.66);
  mist.addColorStop(0, "#88799689");
  mist.addColorStop(0.42, "#564e9b55");
  mist.addColorStop(0.75, "#9272c120");
  mist.addColorStop(1, "#51457400");
  optics.fillStyle = mist;
  optics.fillRect(0, 0, width, height);

  const illumination = optics.createConicGradient(-1.3 + hue / 5, x, y);
  for (const [at, color] of [[0, "#f8c877"], [0.13, "#d989b7"], [0.34, "#6360bf"], [0.53, "#8dc9d7"], [0.68, "#95ccab"], [0.84, "#b091ea"], [1, "#f8c877"]]) {
    illumination.addColorStop(at, color);
  }
  const shape = (angle, amount) => {
    const deviation = samples.length ? samples[Math.floor(angle / (2 * Math.PI) * samples.length) % samples.length] : 0;
    const rim = 1 + 0.072 * Math.sin(3 * angle + hue) + 0.034 * Math.cos(6 * angle - hue) + 0.035 * deviation;
    const r = radius * amount * rim;
    return [x + r * Math.cos(angle) * 1.13, y + r * Math.sin(angle) * 0.83];
  };
  const contour = (amount, backward = false) => {
    for (let i = 0; i <= 240; i++) {
      const angle = (backward ? 240 - i : i) / 240 * 2 * Math.PI;
      const point = shape(angle, amount);
      if (!i) optics.moveTo(...point);
      else optics.lineTo(...point);
    }
    optics.closePath();
  };
  optics.save();
  optics.beginPath();
  contour(1.06);
  contour(0.42, true);
  optics.shadowColor = "#b6a0ff";
  optics.shadowBlur = 28;
  optics.globalAlpha = 0.76;
  optics.fillStyle = illumination;
  optics.fill("evenodd");
  optics.restore();

  optics.save();
  optics.beginPath();
  contour(1.06);
  optics.clip();
  const shading = optics.createRadialGradient(x, y, radius * 0.1, x, y, radius * 1.16);
  shading.addColorStop(0, "#171621");
  shading.addColorStop(0.39, "#171621f7");
  shading.addColorStop(0.59, "#231f45ab");
  shading.addColorStop(0.78, "#251e4a21");
  shading.addColorStop(0.94, "#efddf51a");
  shading.addColorStop(1, "#29213b91");
  optics.fillStyle = shading;
  optics.fillRect(0, 0, width, height);

  for (let i = 0; i < 370; i++) {
    const a = i / 370 * 2 * Math.PI;
    const stroke = Math.sin(i * 15.719 + 0.71) * 0.5 + 0.5;
    const p0 = shape(a, 0.43 + stroke * 0.08);
    const p1 = shape((a + 0.073) % (Math.PI * 2), 0.70 + stroke * 0.05);
    const p2 = shape((a + 0.024) % (Math.PI * 2), 0.99 + stroke * 0.073);
    optics.beginPath();
    optics.moveTo(...p0);
    optics.quadraticCurveTo(...p1, ...p2);
    optics.strokeStyle = i % 5 ? `rgba(248,235,251,${0.05 + stroke * 0.23})` : "#30222e44";
    optics.lineWidth = 0.57 + stroke * 0.66;
    optics.stroke();
  }
  optics.restore();

  for (let i = 0; i < 2100; i++) {
    const px = ((i * 173) % 2111) / 2111 * width;
    const py = ((i * 937) % 2113) / 2113 * height;
    const distance = Math.hypot((px - x) / 1.13, (py - y) / 0.83) / radius;
    if (distance < 0.42 || distance > 1.12) continue;
    optics.fillStyle = i % 3 ? "#fff3" : "#170e3e52";
    optics.fillRect(px, py, 0.8, 0.8);
  }
  if (animate && !motion.matches) {
    prism.getAnimations().forEach((animation) => animation.cancel());
    const opacity = Number(getComputedStyle(prism).opacity);
    prism.animate([{ opacity: opacity * 0.25, transform: "scale(.97) rotate(-3deg)" }, { opacity, transform: "scale(1) rotate(0)" }], {
      duration: 590, easing: "cubic-bezier(.2,.75,.25,1)",
    });
  }
}
new ResizeObserver(() => redrawPrism()).observe(prism);
initialize();
