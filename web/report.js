export const finite = Number.isFinite;
export const list = (value) => Array.isArray(value) ? value : [];
export const object = (value) => value && typeof value === "object" && !Array.isArray(value);
const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
const signed = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2, signDisplay: "exceptZero" });
const money = new Intl.NumberFormat("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const dateFormat = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Moscow" });
const shortDate = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "Europe/Moscow" });
const localDate = new Intl.DateTimeFormat("sv-SE", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Europe/Moscow" });
export const num = (n) => finite(n) ? number.format(n) : "—";
export const pct = (n, sign = false) => finite(n) ? (sign ? signed : number).format(n) + "%" : "—";
export const rub = (n, sign = false) => finite(n) ? (sign && n > 0 ? "+" : "") + money.format(n) : "—";
export const date = (value, brief = false) => finite(Date.parse(value)) ? (brief ? shortDate : dateFormat).format(Date.parse(value)) : "—";
export const dates = (from, to) => finite(Date.parse(from)) && finite(Date.parse(to))
  ? dateFormat.formatRange(Date.parse(from), Date.parse(to)) : "—";
export const tone = (n) => n > 0 ? "positive" : n < 0 ? "negative" : "";
export const factor = (m) => m.no_losses ? "∞" : num(m.profit_factor);
const plural = new Intl.PluralRules("ru-RU");
const sessions = (n) => num(n) + " " + ({ one: "сессия", few: "сессии", many: "сессий" }[plural.select(n)] || "сессий");
export const periodLabels = { 6: "6 мес.", 12: "1 год", 24: "2 года", 60: "5 лет", 120: "10 лет", base: "Основной прогон" };
export const sources = { synthetic: "Синтетические данные", moex: "Мосбиржа · ISS", archive: "Архив Мосбиржи", csv: "Импорт CSV" };
const studyColors = {
  trend: ["Тренд · 5 сессий", "#784cbd"], ridge: ["Ridge · 5 сессий", "#b76172"],
  rotation: ["Недельная ротация", "#4a63ba"], pullback: ["Покупка отката", "#956344"],
};
const intradayColors = {
  hybrid: ["События + рынок", "#784cbd"], market: ["Только рынок", "#3b8375"], momentum: ["Импульс цены", "#956344"],
};

function periods(items, fresh) {
  return [
    ...list(items).filter((row) => row?.available).map((row) => [String(row.months), row]),
    ...(fresh ? [["recent", fresh]] : []),
  ];
}

export function workbench(report) {
  const study = Object.entries(studyColors).flatMap(([key, [label, color]]) => {
    const profile = report.long_study?.strategies?.[key];
    if (!profile) return [];
    const windows = periods(profile.horizons, profile.fresh).map(([id, w]) => ({
      id, ...w, label: id === "recent" ? sessions(w.sessions) : periodLabels[id],
      drawdown: w.drawdown_pct, trades: w.trades,
      equity: list(w.equity).map(([date, equity, drawdown_pct]) => ({ date, equity, drawdown_pct })),
    }));
    return windows.length ? [{ key, label, color, windows, annual: profile.annual, rolling: profile.rolling_summary,
      annualSummary: profile.annual_summary, group: "Многодневные" }] : [];
  });
  const daily = Object.entries(intradayColors).flatMap(([key, [label, color]]) => {
    const profile = report.strategies[key];
    if (!profile?.metrics) return [];
    const windows = key === report.preset?.agent ? periods(report.horizons).map(([id, w]) => ({
      id, label: periodLabels[id], ...w.metrics, start: w.start, end: w.end, stress_pct: w.stress_return_pct,
      drawdown: w.metrics.max_drawdown_pct, equity: list(w.equity).map(([date, equity]) => ({ date, equity })),
    })) : [];
    windows.unshift({
      id: "base", label: periodLabels.base, ...profile.metrics, start: report.meta.oos_start, end: report.meta.oos_end,
      drawdown: profile.metrics.max_drawdown_pct, equity: list(profile.equity), trades: profile.trades,
    });
    return [{ key, label, color, windows, group: "Внутридневные" }];
  });
  return [...study, ...daily];
}

export function validate(report) {
  if (report?.schema_version !== 1 || !object(report.strategies) || !object(report.meta) || !workbench(report).length) {
    throw new Error("Выберите файл report.json из Контура.");
  }
  return report;
}

export function auditOverlay(audit, window) {
  const marks = list(audit.equity);
  const daily = new Map(marks.map((m) => [localDate.format(Date.parse(m.timestamp)), m]));
  const sample = list(window?.equity).filter(({ date, equity }) => {
    const row = daily.get(date);
    return row?.equity > 0 && equity > 0 && object(row.open_positions) && !Object.keys(row.open_positions).length;
  });
  if (sample.length < 2) return { account: [...daily].map(([date, row]) => ({ date, equity: row.equity })) };
  const first = sample[0];
  const baseline = daily.get(first.date).equity;
  const model = sample.map(({ date, equity }) => ({ date, equity: baseline * equity / first.equity }));
  const account = sample.map(({ date }) => ({ date, equity: daily.get(date).equity }));
  const drift = 100 * (account.at(-1).equity - model.at(-1).equity) / baseline;
  return { account, model, drift, baseline };
}

export function download(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function exportTrades(items, key) {
  const columns = ["symbol", "entry", "exit", "net_pnl", "reason"];
  const cell = (value) => {
    let text = String(value ?? "");
    if (typeof value === "string" && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  const values = items.map((t) => [t.symbol || t.ticker, t.entry_day || t.entry_at,
    t.exit_day || t.exit_at, t.net_pnl ?? t.pnl, t.reason]);
  download("kontur-" + key + "-trades.csv", "\ufeff" + [columns.join(","), ...values.map((row) => row.map(cell).join(","))].join("\r\n"), "text/csv;charset=utf-8");
}
