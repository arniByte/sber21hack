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
const sources = { synthetic: "Синтетические данные", moex: "Котировки MOEX", archive: "Рыночный архив", csv: "Импорт CSV" };
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
  strategyKey = Object.hasOwn(strategies, input.preset?.agent) && input.strategies[input.preset.agent] ? input.preset.agent : "hybrid";
  metricKey = "equity";
  rangeDays = 0;
  filterDay = "";
  showAll = false;
  showNotice("");
  closeAnswer(false);

  const meta = report.meta;
  $("share-profile").hidden = !record(report.preset?.risk);
  setText("profile-name", report.preset?.profile ? "Профиль · " + report.preset.profile : "Отчёт стратегии");
  renderWindows();
  const decadeTab = document.querySelector('[data-view="decade"]');
  decadeTab.hidden = !record(report.long_study?.strategies);
  if (decadeTab.hidden && !$("decade").hidden) document.querySelector('[data-view="performance"]').click();
  renderDecade("trend");
  renderMovers();
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
  if (!topic) closeAnswer(false);
  if (topic) showAnswer(topic);
  $("prism").dataset.agent = strategyKey;
  renderAccount();
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
    ["Цель Ridge", report.preset?.objective === "exit" ? "До выхода по стопу, цели или закрытию" : "От открытия до закрытия"],
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
    if (id === loadId) {
      setReport(JSON.parse(text));
      $("ask-mishka").hidden = true;
    }
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
    const capabilities = await fetch("/api/capabilities").then((res) => res.ok ? res.json() : {}).catch(() => ({}));
    $("ask-mishka").hidden = loadId > 0 || capabilities.mishka !== true;
  } catch {
    if (loadId) return;
    setText("report-summary", "Откройте сохранённый JSON-отчёт.");
    showNotice("Отчёт по умолчанию недоступен. Выберите файл через «Открыть JSON».");
  }
}

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => {
    for (const item of document.querySelectorAll("[data-view]")) {
      const selected = item === button;
      item.setAttribute("aria-pressed", String(selected));
      $(item.dataset.view).hidden = !selected;
    }
  });
}

function renderWindows() {
  const list = $("window-list");
  list.replaceChildren();
  const windows = rows(report.horizons);
  if (!windows.length) {
    setText("windows-caption", "Для сравнения сроков пересчитайте отчёт с флагом --horizons.");
    return;
  }
  const label = strategies[report.preset?.agent]?.label || "";
  setText("windows-caption", [report.preset?.profile, label, "Каждый срок начинается с " + rub(report.meta.start_capital)].filter(Boolean).join(" · "));
  for (const w of windows) {
    const row = add("details", list, undefined, "window-row");
    const summary = add("summary", row);
    const title = {6: "6 месяцев", 12: "1 год", 24: "2 года", 60: "5 лет"}[w.months];
    add("strong", summary, title);
    if (!w.available || !record(w.metrics)) {
      add("span", summary, "Мало истории");
      add("p", row, "Для этого срока не хватает данных до начала окна обучения.");
      continue;
    }
    const m = w.metrics;
    const dates = dateLabel(w.start, fullDateFormat) + " — " + dateLabel(w.end, fullDateFormat);
    add("span", summary, dates, "window-date");
    add("strong", summary, pct(m.return_pct), tone(m.return_pct));
    const grid = add("dl", row, undefined, "window-metrics");
    for (const [name, value] of [
      ["Чистый результат", signedRub(m.net_pnl)], ["Просадка", pct(m.max_drawdown_pct)],
      ["Profit Factor", m.no_losses ? "∞" : num(m.profit_factor)], ["Ситуации", num(m.trade_count)],
      ["Месяцы в плюсе¹", num(w.monthly?.positive) + " / " + num(w.monthly?.count)], ["Худший месяц¹", pct(w.monthly?.worst_pct)],
      ["При удвоенных издержках", pct(w.stress_return_pct)], ["Остановка входов", m.risk_halt_date ? dateLabel(m.risk_halt_date, fullDateFormat) : "Лимит не достигнут"],
      ["Просадка · из 25 баллов", num(m.score_components?.drawdown)],
      ["Profit Factor · из 15 баллов", num(m.score_components?.profit_factor)],
    ]) { const pair = add("div", grid); add("dt", pair, name); add("dd", pair, value); }
    add("p", row, dates, "window-mobile-date");
    const equity = Array.isArray(w.equity) ? w.equity.filter((p) =>
      Array.isArray(p) && finite(Date.parse(p[0])) && finite(p[1])) : [];
    if (equity.length > 1 && finite(report.meta.start_capital) && report.meta.start_capital > 0) {
      const series = equity.map(([day, value]) => ({time: Date.parse(day), capital: 100 * (value / report.meta.start_capital - 1)}));
      renderMiniChart(row, "Доходность. " + title + ". " + pct(m.return_pct), series,
        [["capital", "#6950ca"]], Date.parse(m.risk_halt_date));
    }
    add("p", row, "¹ Учтены неполные месяцы на границах и месяцы без сделок. Баллы расчётные. Для конкурсной оценки нужны реальные исполнения.");
  }
}

for (const button of document.querySelectorAll("[data-study]")) {
  button.addEventListener("click", () => renderDecade(button.dataset.study));
}

function renderDecade(key) {
  const study = report.long_study;
  const model = study?.strategies?.[key];
  const output = $("decade-results");
  output.replaceChildren();
  if (!model) return;
  for (const button of document.querySelectorAll("[data-study]")) {
    button.hidden = !record(study.strategies[button.dataset.study]);
    button.setAttribute("aria-pressed", String(button.dataset.study === key));
  }
  const rules = model.rules;
  const holding = rules?.sessions === null ? "Решение раз в неделю" : "До " + (rules?.sessions ?? 5) + " сессий в позиции";
  const cap = finite(rules?.position) ? " · До " + pct(rules.position * 100) + " капитала в позиции" : "";
  setText("decade-period", "Дневные свечи по " + dateLabel(study.sample_end, fullDateFormat) + " · " + holding + cap);
  const annual = model.annual_summary;
  const rolling = model.rolling_summary;
  const grid = add("div", output, undefined, "study-summary");
  for (const [label, item] of [["Независимые годы", annual], ["Скользящие 12 месяцев", rolling]]) {
    if (!item?.count) continue;
    const card = add("div", grid);
    add("h3", card, label);
    add("strong", card, num(item.profitable) + " / " + num(item.count));
    add("p", card, "Периоды в плюсе");
    if (item.worst) add("p", card, "Худший результат " + pct(item.worst.return_pct) +
      " · до " + dateLabel(item.worst.end, fullDateFormat));
  }
  const capital = report.meta.start_capital;
  const labels = {6: "6 месяцев", 12: "1 год", 24: "2 года", 60: "5 лет", 120: "10 лет"};
  const horizons = rows(model.horizons).reverse();
  if (record(model.fresh)) horizons.unshift({...model.fresh, available: true});
  for (const w of horizons) {
    const item = add("details", output, undefined, "window-row");
    const summary = add("summary", item);
    const label = labels[w.months] || "Свежий период";
    add("strong", summary, label);
    if (!w.available) { add("span", summary, "Мало истории"); continue; }
    add("strong", summary, pct(w.return_pct), tone(w.return_pct));
    const metrics = add("dl", item, undefined, "window-metrics");
    for (const [name, value] of [
      ["Чистый результат", signedRub(w.net_pnl)], ["Капитал на конец", rub(capital + w.net_pnl)],
      ...(finite(w.cagr_pct) ? [["Годовой темп (CAGR)", pct(w.cagr_pct)]] : []),
      ["Фиксированный размер от старта", pct(w.fixed_size_pct)], ["Двойные издержки", pct(w.stress_pct)],
      ...(finite(w.matched_risk_pct) ? [["При бюджете риска пятисессионного тренда", pct(w.matched_risk_pct)]] : []),
      ["Просадка", pct(w.drawdown_pct)], ["Profit Factor", w.no_losses ? "∞" : num(w.profit_factor)],
      ["Закрыто позиций", num(w.trade_count)], ["Сессии в позиции", num(w.exposed_days) + " / " + num(w.sessions)],
      ["Доля лучшей сделки", pct(w.concentration_pct)],
      ["Просадка · из 25 баллов", num(w.drawdown_points)], ["Profit Factor · из 15 баллов", num(w.pf_points)],
    ]) {
      const pair = add("div", metrics);
      add("dt", pair, name); add("dd", pair, value);
    }
    add("p", item, dateLabel(w.start, fullDateFormat) + " — " + dateLabel(w.end, fullDateFormat));
    if (finite(capital) && capital > 0 && w.equity?.length > 1) {
      const series = w.equity.map(([day, value]) => ({time: Date.parse(day), value: 100 * (value / capital - 1)}));
      renderMiniChart(item, label + " · Доходность " + pct(w.return_pct), series,
        [["value", "#6950ca"]], Date.parse(w.halt));
    }
  }
  const annualDetails = add("details", output, undefined, "window-row");
  add("summary", annualDetails, "Результаты по каждому году");
  const list = add("ol", annualDetails, undefined, "study-years");
  for (const w of rows(model.annual)) {
    const line = add("li", list);
    add("span", line, dateLabel(w.end, fullDateFormat));
    add("strong", line, pct(w.return_pct), tone(w.return_pct));
  }
}

function renderMovers() {
  const m = report.movers;
  setText("market-caption", m ? dateLabel(m.previous) + " → " + dateLabel(m.as_of, fullDateFormat) +
    " · По ценам закрытия · Бумаг в сравнении: " + num(m.count) : "Для сравнения нужны две торговые сессии.");
  for (const group of ["gainers", "losers"]) {
    const list = $(group);
    list.replaceChildren();
    const items = rows(m?.[group]);
    if (!items.length) add("li", list, "Нет бумаг в этой группе.");
    for (const item of items) {
      const row = add("li", list);
      const name = add("span", row, item.name);
      add("small", name, item.symbol);
      add("b", row, (item.change_pct > 0 ? "+" : "") + pct(item.change_pct), tone(item.change_pct));
    }
  }
}

$("share-profile").addEventListener("click", async () => {
  const payload = {...report.preset, agent: strategyKey};
  const file = new File([JSON.stringify(payload, null, 2)], "kontur-" + strategyKey + ".json", {type: "application/json"});
  if (navigator.canShare?.({files: [file]})) {
    try { await navigator.share({files: [file], title: "Контур · " + payload.profile}); return; }
    catch (error) { if (error.name === "AbortError") return; }
  }
  const url = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = url;
  link.download = file.name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

let audit;
function compareAccount(body) {
  const df = new Intl.DateTimeFormat("sv-SE", {timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit"});
  const daily = new Map(audit.equity.map((mark) => [df.format(new Date(mark.timestamp)), mark]));
  const matching = rows(strategy()?.equity).filter((point) => {
    const mark = daily.get(point.date);
    return mark && record(mark.open_positions) && !Object.keys(mark.open_positions).length && point.equity > 0 && mark.equity > 0;
  });
  if (matching.length < 2) {
    add("p", body, "Для сравнения динамики нужны две общие даты с закрытыми позициями на последней отметке счёта.");
    return;
  }
  const first = matching[0], last = matching.at(-1);
  const liveStart = daily.get(first.date).equity;
  const series = matching.map((point) => ({time: Date.parse(point.date),
    model: 100 * (point.equity / first.equity - 1), account: 100 * (daily.get(point.date).equity / liveStart - 1)}));
  const end = series.at(-1);
  const drift = end.account - end.model;
  const caption = "Счёт " + pct(end.account) + " · Модель " + pct(end.model) +
    " · Разница " + num(drift) + " п.п.";
  add("h3", body, "Счёт и симуляция", "compare-title");
  add("p", body, dateLabel(first.date, fullDateFormat) + " — " + dateLabel(last.date, fullDateFormat) +
    ". От общей начальной отметки. " + caption);
  renderMiniChart(body, caption, series, [["model", "#6950ca"], ["account", "#237a72"]]);
  add("p", body, "Зелёная линия — счёт, фиолетовая — модель. Учтены последние доступные отметки в совпавшие дни.");
}

function renderMiniChart(parent, caption, series, lines, halt) {
  const canvas = add("canvas", parent, undefined, "mini-chart");
  canvas.width = 1000; canvas.height = 180;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", caption);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  let min = 0, max = 0;
  for (const point of series) {
    for (const [key] of lines) {
      min = Math.min(min, point[key]);
      max = Math.max(max, point[key]);
    }
  }
  const start = series[0].time;
  const duration = series.at(-1).time - start || 1;
  const x = (time) => 4 + 992 * (time - start) / duration;
  const y = (value) => max === min ? 90 : 168 - 156 * (value-min)/(max-min);
  ctx.strokeStyle = "#dadbd5"; ctx.beginPath(); ctx.moveTo(0, y(0)); ctx.lineTo(1000, y(0)); ctx.stroke();
  for (const [key, color] of lines) {
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.beginPath();
    series.forEach((v, i) => i ? ctx.lineTo(x(v.time), y(v[key])) : ctx.moveTo(x(v.time), y(v[key])));
    ctx.stroke();
  }
  if (finite(halt)) {
    const point = series.find((v) => v.time === halt);
    if (!point) return;
    ctx.strokeStyle = "#a33945";
    ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.moveTo(x(halt), 6); ctx.lineTo(x(halt), 174); ctx.stroke();
    add("p", parent, "Пунктир отмечает остановку входов.");
  }
}

function renderAccount() {
  if (!audit) return;
  const body = $("account-content");
  body.replaceChildren();
  const m = audit.metrics;
  add("p", body, dateLabel(audit.start, fullDateFormat) + " — " + dateLabel(audit.end, fullDateFormat));
  const grid = add("dl", body, undefined, "window-metrics");
  for (const [name, value] of [["Результат счёта", signedRub(m.net_pnl)], ["Просадка по отметкам", pct(m.max_drawdown_pct)],
      ["Закрытые ситуации", num(m.trade_count)], ["Торговые дни", num(m.trade_days)], ["Profit Factor", m.no_losses ? "∞" : num(m.profit_factor)]]) {
    const entry = add("div", grid); add("dt", entry, name); add("dd", entry, value);
  }
  if (record(audit.contest?.components)) {
    const score = add("details", body, undefined, "window-row");
    add("summary", score, "Предварительная оценка");
    const scores = add("dl", score, undefined, "window-metrics");
    const components = audit.contest.components;
    for (const [name, points, max] of [
      ["Финансовый результат", components.financial_result, 40], ["Просадка", components.drawdown, 25],
      ["Profit Factor", components.profit_factor, 15], ["Повторяемость", components.repeatability, 10],
      ["Качество стратегии", components.strategy_quality, 10],
    ]) {
      const entry = add("div", scores); add("dt", entry, name); add("dd", entry, num(points) + " / " + max);
    }
    if (audit.contest.concentration_review) {
      add("p", score, "Более 60% чистой прибыли получено в одной ситуации. Повторяемость требует отдельного подтверждения.");
    }
  }
  const limit = strategy()?.risk?.max_drawdown_pct;
  if (finite(limit)) add("p", body, "Лимит просадки выбранного профиля " + pct(limit) +
    (m.max_drawdown_pct >= limit ? " достигнут." : " пока не достигнут."));
  compareAccount(body);
  const history = add("details", body, undefined, "window-row");
  add("summary", history, "История переоценки · " + num(audit.equity.length));
  const list = add("ol", history, undefined, "account-marks");
  for (const mark of audit.equity) {
    const item = add("li", list);
    add("span", item, dateLabel(mark.timestamp, timeFormat));
    add("strong", item, rub(mark.equity));
  }
}
$("open-audit").addEventListener("click", () => $("audit-file").click());
$("audit-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (data.metrics?.origin !== "execution_audit" || !finite(data.metrics.net_pnl) || !Array.isArray(data.equity) ||
        !data.equity.length || !data.equity.every((p) => finite(p.equity) && Number.isFinite(Date.parse(p.timestamp)))) throw Error();
    audit = data;
    renderAccount();
    showNotice("");
  } catch { showNotice("Нужен JSON с результатом команды kontur audit."); }
});

$("ask-mishka").addEventListener("click", async () => {
  const question = $("question").value.trim();
  if (!question) { $("question").focus(); return; }
  const button = $("ask-mishka");
  const currentReport = report;
  const agent = strategyKey;
  topic = undefined;
  button.disabled = true;
  $("answer").hidden = false;
  setText("answer-title", "Мишка · OpenRouter");
  setText("answer-content", "Читает показатели отчёта…");
  try {
    const response = await fetch("/api/mishka", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question, agent})});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || "OpenRouter недоступен.");
    if (currentReport === report && agent === strategyKey && !topic) setText("answer-content", data.answer);
  } catch (error) {
    if (currentReport === report && agent === strategyKey && !topic) setText("answer-content", error.message);
  } finally { button.disabled = false; }
});
initialize();
