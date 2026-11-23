import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { KonturChart } from "./charts.js";
import OrbScene from "./OrbScene.jsx";
import { auditOverlay, date, dates, download, exportTrades, factor, finite, list, num, object,
  pct, rub, sources, tone, validate, workbench } from "./report.js";

const tabs = [["overview", "Обзор"], ["market", "Рынок"], ["account", "Счёт"], ["analysis", "Мишка"]];
const exits = { stop: "Стоп", take_profit: "Цель", close: "Закрытие", gap: "Гэп", rotation: "Ротация" };
const signals = [ ["equity", "Капитал"], ["drawdown", "Просадка"], ["outcomes", "По сделкам"] ];
const topics = ["Сравни стратегии", "Что с риском?", "Откуда данные?"];
const tradeDay = (t) => t.trade_day || t.exit_day || t.entry_at?.slice(0, 10);
const pnl = (t) => t.net_pnl ?? t.pnl;

function Chart({ lines, metric = "equity", capital, trades = [], onDay }) {
  const canvas = useRef(null);
  const tooltip = useRef(null);
  const live = useRef(null);
  const chart = useRef(null);
  const choose = useRef(onDay);
  choose.current = onDay;
  useEffect(() => {
    const instance = new KonturChart(canvas.current, tooltip.current, live.current, (day) => choose.current?.(day));
    chart.current = instance;
    return () => instance.destroy();
  }, []);
  useEffect(() => {
    chart.current?.update({ strategies: lines, selected: lines[0]?.key, metric, firstCapital: capital, trades });
  }, [lines, metric, capital, trades]);
  return <div className="plot">
    <canvas ref={canvas} tabIndex={0} role="img" aria-label="График" />
    <span ref={live} aria-live="polite" className="sr-only" />
    <div ref={tooltip} className="chart-tip" hidden />
  </div>;
}

function Metrics({ values, className = "" }) {
  return <dl className={"metrics " + className}>{values.map(([name, value, color, detail]) =>
    <div key={name}><dt>{name}</dt><dd className={color || ""}>{value}</dd>{detail && <span>{detail}</span>}</div>)}</dl>;
}

function Detail({ title, hint, children, reference }) {
  return <details ref={reference} className="detail">
    <summary><span>{title}</span>{hint && <small>{hint}</small>}<i aria-hidden="true" /></summary>
    <div className="detail-body">{children}</div>
  </details>;
}

function FileButton({ children, onData, className = "secondary" }) {
  const input = useRef(null);
  return <>
    <button type="button" className={className} onClick={() => input.current.click()}>{children}</button>
    <input ref={input} className="sr-only" type="file" accept=".json,application/json" tabIndex={-1}
      aria-label={children} onChange={async (e) => {
        const file = e.currentTarget.files[0];
        e.currentTarget.value = "";
        if (!file) return;
        try { onData(JSON.parse(await file.text())); } catch (error) { onData(null, error); }
      }} />
  </>;
}

function Trades({ trades, day, clear, strategy }) {
  const [expanded, setExpanded] = useState(false);
  if (!Array.isArray(trades)) return <p className="muted">Журнал доступен в отчётах с детализацией сделок.</p>;
  const matching = day ? trades.filter((t) => tradeDay(t) === day) : trades;
  const shown = expanded ? matching : matching.slice(-6);
  return <>
    <div className="detail-actions">
      {day && <button className="secondary" onClick={clear}>{date(day, true)} · Все даты</button>}
      {matching.length > 0 && <button className="secondary" onClick={() => exportTrades(matching, strategy)}>Скачать CSV</button>}
    </div>
    {!matching.length ? <p className="muted">{day ? "В этот день сделки не закрывались." : "На этом отрезке сделок не было."}</p> :
      <table className="report-table"><thead><tr><th>Бумага</th><th>Закрытие</th><th>Результат</th></tr></thead>
        <tbody>{shown.map((t, i) => <tr key={i}>
          <th scope="row">{t.symbol || t.ticker}<small>{exits[t.reason] || "Выход"}</small></th>
          <td>{date(tradeDay(t), true)}</td><td className={tone(pnl(t))}>{rub(pnl(t), true)}</td>
        </tr>)}</tbody></table>}
    {matching.length > 6 && <button className="secondary more" onClick={() => setExpanded(!expanded)}>{expanded ? "Свернуть" : "Все сделки"}</button>}
  </>;
}

function Overview({ report, profiles, strategy, period, setStrategy, setPeriod }) {
  const [metric, setMetric] = useState("equity");
  const [day, setDay] = useState("");
  const detail = useRef(null);
  const hasDrawdown = period.equity.some((row) => finite(row.drawdown_pct));
  const resolvedMetric = metric === "drawdown" && !hasDrawdown || metric === "outcomes" && !period.trades?.length ? "equity" : metric;
  const lines = useMemo(() => [{ ...strategy, equity: period.equity }], [strategy, period]);
  const chartTrades = useMemo(() => list(period.trades).map((t) => ({ trade_day: tradeDay(t), pnl: pnl(t) })), [period]);
  const inputs = report.meta;
  const periodStart = inputs.sample_end || period.end;
  const riskPart = [
    ["Profit Factor", factor(period)],
    ["При двойных издержках", pct(period.stress_pct, true), tone(period.stress_pct)],
    ["Доля лучшей сделки", pct(period.concentration_pct)],
    ["Годовой темп", pct(period.cagr_pct, true)],
    ["При равном бюджете риска", pct(period.matched_risk_pct, true)],
    ["С фиксированным размером", pct(period.fixed_size_pct, true)],
    ...(finite(period.drawdown_points ?? period.score_components?.drawdown)
      ? [["Баллы за просадку", num(period.drawdown_points ?? period.score_components.drawdown) + " / 25"]] : []),
    ...(finite(period.pf_points ?? period.score_components?.profit_factor)
      ? [["Баллы за Profit Factor", num(period.pf_points ?? period.score_components.profit_factor) + " / 15"]] : []),
    ["Остановка входов", period.halt || period.risk_halt_date ? date(period.halt || period.risk_halt_date) : "Лимит не достигнут"],
  ];
  const chooseDay = (session) => {
    if (!Array.isArray(period.trades)) return;
    setDay(session);
    detail.current.open = true;
    detail.current.querySelector("summary")?.focus();
    detail.current.scrollIntoView({ block: "nearest" });
  };
  return <>
    <section className="hero" aria-labelledby="hero-title">
      <OrbScene />
      <div className="hero-text">
        <span className="source"><i aria-hidden="true" />{sources[inputs.dataset] || "Исторические данные"}</span>
        <h1 id="hero-title">Торговые<br />стратегии</h1>
        <p>Данные по {date(periodStart)}</p>
      </div>
    </section>
    <section className="summary" aria-label="Результат бэктеста">
      <Metrics className="main-metrics" values={[
        ["Доходность", pct(period.return_pct, true), tone(period.return_pct), rub(period.net_pnl, true) + " после издержек"],
        ["Просадка", pct(period.drawdown)],
        ["Сделки", num(period.trade_count)],
      ]} />
    </section>
    <section className="surface chart-card" aria-labelledby="capital-title">
      <div className="section-head">
        <div><h2 id="capital-title" className="sr-only">Динамика стратегии</h2>
          <label className="select-wrap chart-heading"><span className="sr-only">Виды графика</span>
            <select value={resolvedMetric} onChange={(e) => setMetric(e.target.value)}>
              {signals.filter(([id]) => id === "equity" || (id === "drawdown" && hasDrawdown) || (id === "outcomes" && period.trades?.length)).map(([id, label]) =>
                <option key={id} value={id}>{label}</option>)}
            </select>
          </label><p>{dates(period.start, period.end)}</p></div>
        <label className="select-wrap"><span className="sr-only">Стратегия</span>
          <select value={strategy.key} onChange={(e) => { setDay(""); setStrategy(e.target.value); }}>
            {["Многодневные", "Внутридневные"].filter((label) => profiles.some((p) => p.group === label)).map((label) =>
              <optgroup key={label} label={label}>{profiles.filter((p) => p.group === label).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}</optgroup>)}
          </select></label>
      </div>
      <div className="chart-controls">
        <div className="segments" role="group" aria-label="Период">{strategy.windows.map((w) =>
          <button key={w.id} aria-pressed={w.id === period.id} onClick={() => { setDay(""); setPeriod(w.id); }}>{w.label}</button>)}</div>

      </div>
      <Chart lines={lines} metric={resolvedMetric} capital={inputs.start_capital} trades={chartTrades} onDay={chooseDay} />
    </section>
    <section className="details surface" aria-label="Подробности">
      <Detail title="Риск и издержки">
        <Metrics values={riskPart.filter(([, value]) => value !== "—")} />
      </Detail>
      {strategy.annual?.length > 0 && <Detail title="Результаты по годам" hint={strategy.annualSummary?.profitable + " / " + strategy.annualSummary?.count + " в плюсе"}>
        <Metrics values={[
          ["Скользящие окна в плюсе", num(strategy.rolling?.profitable) + " / " + num(strategy.rolling?.count)],
          ["Худшее окно", pct(strategy.rolling?.worst?.return_pct, true), tone(strategy.rolling?.worst?.return_pct)],
        ]} />
        <table className="report-table"><thead><tr><th>Период</th><th>После издержек</th></tr></thead>
          <tbody>{strategy.annual.map((row) => <tr key={row.start}><th scope="row">{dates(row.start, row.end)}</th><td className={tone(row.return_pct)}>{pct(row.return_pct, true)}</td></tr>)}</tbody></table>
      </Detail>}
      <Detail title="Журнал сделок" reference={detail}>
        <Trades key={strategy.key + period.id + day} trades={period.trades} day={day} clear={() => setDay("")} strategy={strategy.key} />
      </Detail>
      <Detail title="Данные и методика">
        <Metrics values={[
          ["Свечи", num(inputs.bars)], ["События", num(inputs.events)], ["Бумаги", list(inputs.symbols).join(", ") || "—"],
        ]} />
        <p className="muted">График показывает историческую симуляцию. В отчёте учтены комиссии и проскальзывание.</p>
        {list(report.events).length > 0 && <ul className="events">{report.events.slice(0, 5).map((event, i) =>
          <li key={i}>{/^https?:\/\//i.test(event?.url || "") ?
            <a href={event.url} target="_blank" rel="noreferrer">{event.title}</a> : event?.title}
            {" "}<small>{date(event?.published_at, true)}</small></li>)}</ul>}
        {strategy.group === "Внутридневные" && object(report.preset?.risk) && <button className="secondary" onClick={() =>
          download("kontur-" + strategy.key + ".json", JSON.stringify({ ...report.preset, agent: strategy.key }, null, 2), "application/json")}>Скачать пресет</button>}
      </Detail>
    </section>
  </>;
}

function Market({ moves }) {
  if (!moves) return <section className="surface secondary-page"><h1>Рынок</h1><p className="muted">В этом отчёте нет рыночного снимка.</p></section>;
  return <section className="surface secondary-page"><h1>Рынок</h1><p className="muted">{dates(moves.previous, moves.as_of)} · {num(moves.count)} бумаг</p>
    <div className="market-grid">{[["Лидеры роста", moves.gainers], ["Лидеры снижения", moves.losers]].map(([label, items]) =>
      <div key={label}><h2>{label}</h2><ul className="leader-list">{list(items).map((item) =>
        <li key={item.symbol}><span>{item.name}<small>{item.symbol}</small></span>
          <strong className={tone(item.change_pct)}>{pct(item.change_pct, true)}</strong></li>)}</ul></div>)}</div>
  </section>;
}

function Account({ audit, setAudit, period }) {
  const [error, setError] = useState("");
  const overlay = useMemo(() => audit ? auditOverlay(audit, period) : null, [audit, period]);
  const lines = useMemo(() => !overlay ? [] : [
    { key: "account", label: "Счёт", color: "#18806d", equity: overlay.account },
    ...(overlay.model ? [{ key: "model", label: "Симуляция", color: "#9b73bf", equity: overlay.model }] : []),
  ], [overlay]);
  const metrics = audit?.metrics;
  return <section className="surface secondary-page">
    <div className="section-head"><div><h1>Реальный счёт</h1>{audit && <p>{dates(audit.start, audit.end)}</p>}</div>
      <FileButton onData={(next) => {
        if (next?.metrics?.origin !== "execution_audit" || !list(next.equity).length ||
            !next.equity.every((row) => row && finite(row.equity) && finite(Date.parse(row.timestamp)))) {
          setError("Выберите файл execution-audit.json."); return;
        }
        setError(""); setAudit(next);
      }}>Открыть аудит</FileButton></div>
    {error && <p className="error" role="alert">{error}</p>}
    {!metrics ? <p className="muted">Загрузите аудит исполнений для сравнения со стратегией.</p> : <>
      <Metrics values={[
        ["Чистый результат", rub(metrics.net_pnl, true), tone(metrics.net_pnl)], ["Просадка", pct(metrics.max_drawdown_pct)],
        ["Сделки", num(metrics.trade_count)], ["Profit Factor", factor(metrics)],
      ]} />
      <Chart lines={lines} capital={overlay.baseline} />
      {finite(overlay.drift) && <p className="muted">Разница со стратегией на общих датах · {num(overlay.drift)} п.п.</p>}
      <Detail title="История переоценки" hint={num(audit.equity.length)}>
        <div className="table-scroll"><table className="report-table"><thead><tr><th>Отметка</th><th>Капитал</th></tr></thead>
          <tbody>{audit.equity.map((row, i) => <tr key={i}>
            <th scope="row">{date(row.timestamp)} · {new Date(row.timestamp).toLocaleTimeString("ru-RU", {timeZone: "Europe/Moscow", hour: "2-digit", minute: "2-digit"})}</th><td>{rub(row.equity)}</td>
          </tr>)}</tbody></table></div>
      </Detail>
      {object(audit.contest?.components) && <Detail title="Предварительные баллы">
        <Metrics values={[
          ["Прибыль", num(audit.contest.components.financial_result) + " / 40"],
          ["Просадка", num(audit.contest.components.drawdown) + " / 25"],
          ["Profit Factor", num(audit.contest.components.profit_factor) + " / 15"],
          ["Повторяемость", num(audit.contest.components.repeatability) + " / 10"],
          ["Качество", num(audit.contest.components.strategy_quality) + " / 10"],
        ]} />
      </Detail>}
    </>}
  </section>;
}

function Analysis({ report, strategy, period, profiles, live }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(null);
  useEffect(() => () => request.current?.abort(), []);
  function localAnswer(query) {
    if (/риск|просад/iu.test(query)) setAnswer("На этом отрезке максимальная просадка составила " + pct(period.drawdown) + ". Это падение капитала от предыдущего максимума.");
    else if (/данн|источ/iu.test(query)) setAnswer((sources[report.meta.dataset] || "Импорт") + " · " + num(report.meta.bars) + " свечей · " + dates(report.meta.sample_start, report.meta.sample_end) + ".");
    else if (/сравн/iu.test(query)) setAnswer(profiles.flatMap((p) => {
      const w = p.windows.find((w) => w.id === period.id && w.start === period.start && w.end === period.end);
      return w ? [p.label + " · " + pct(w.return_pct, true) + " · просадка " + pct(w.drawdown)] : [];
    }).join("\n"));
    else setAnswer("Офлайн доступны сравнение стратегий, оценка риска и описание данных.");
  }
  async function ask(query) {
    if (!query.trim() || busy) return;
    setQuestion(query);
    if (!live) {
      localAnswer(query);
      return;
    }
    setBusy(true);
    setAnswer("");
    request.current = new AbortController();
    try {
      const response = await fetch("/api/mishka", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: query, agent: strategy.key, period: period.id }), signal: request.current.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Ответ недоступен.");
      setAnswer(data.answer);
    } catch (error) {
      if (error.name !== "AbortError") {
        localAnswer(query);
        setAnswer((text) => (error.message || "Ответ недоступен.") + "\n\n" + text);
      }
    } finally { setBusy(false); }
  }
  return <section className="surface secondary-page ask-page">
    <h1>Мишка</h1><p className="muted">{strategy.label} · {dates(period.start, period.end)} · {live ? "OpenRouter" : "Офлайн-разбор"}</p>
    <form className="ask-form" onSubmit={(e) => { e.preventDefault(); ask(question); }}>
      <input aria-label="Вопрос по отчёту" value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Что разобрать в отчёте?" maxLength={500} />
      <button aria-label="Отправить вопрос" type="submit" disabled={busy || !question.trim()}>↑</button>
    </form>
    <div className="suggestions">{topics.map((q) => <button key={q} className="secondary" onClick={() => ask(q)} disabled={busy}>{q}</button>)}</div>
    <div aria-live="polite" className={answer || busy ? "answer" : ""}>{busy ? "Изучаю отчёт…" : answer}</div>
  </section>;
}

function App() {
  const [report, setReport] = useState(null);
  const [view, setView] = useState("overview");
  const [strategyKey, setStrategy] = useState("");
  const [periodId, setPeriod] = useState("12");
  const [audit, setAudit] = useState(null);
  const [live, setLive] = useState(false);
  const [notice, setNotice] = useState("");
  const [imported, setImported] = useState(false);
  const importedRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    const embedded = document.getElementById("embedded-report");
    const signal = controller.signal;
    const sync = async () => {
      try {
        const data = embedded ? JSON.parse(embedded.textContent) :
          await fetch("/results/report.json", { signal }).then((r) => {
            if (!r.ok) throw new Error();
            return r.json();
          });
        if (!importedRef.current) setReport(validate(data));
      } catch (error) {
        if (!importedRef.current && error.name !== "AbortError") setNotice("Откройте готовый отчёт Контура.");
      }
    };
    sync();
    if (!embedded) fetch("/api/capabilities", { signal }).then((r) => r.json()).then((data) => setLive(data.mishka === true)).catch(() => {});
    return () => controller.abort();
  }, []);

  const profiles = useMemo(() => report ? workbench(report) : [], [report]);
  const strategy = profiles.find((p) => p.key === strategyKey) || profiles.find((p) => p.key === "trend") ||
    profiles.find((p) => p.key === report?.preset?.agent) || profiles[0];
  const period = strategy?.windows.find((p) => p.id === periodId) || strategy?.windows[0];

  const load = (data) => {
    try {
      const next = validate(data);
      importedRef.current = true;
      setImported(true); setNotice(""); setStrategy(""); setPeriod("12"); setView("overview"); setReport(next);
    } catch (error) { setNotice(error.message); }
  };

  return <>
    <a className="skip sr-only" href="#main">К содержимому</a>
    <header className="topbar">
      <div className="top-inner">
        <button className="brand" onClick={() => setView("overview")} aria-label="Контур · Обзор"><i aria-hidden="true" />Контур</button>
        <nav aria-label="Разделы">{tabs.map(([key, name]) =>
          <button key={key} aria-current={view === key ? "page" : undefined} onClick={() => setView(key)}>{name}</button>)}</nav>
        <FileButton onData={load}>Открыть отчёт</FileButton>
      </div>
    </header>
    <main id="main" className="page">
      {notice && <p className="error" role="alert">{notice}</p>}
      {!report && !notice && <p className="muted">Открываем отчёт…</p>}
      {report && <div key={view} className="view">
        {view === "overview" && <Overview report={report} profiles={profiles} strategy={strategy} period={period}
          setStrategy={setStrategy} setPeriod={setPeriod} />}
        {view === "market" && <Market moves={report.movers} />}
        {view === "account" && <Account audit={audit} setAudit={setAudit} period={period} />}
        {view === "analysis" && <Analysis report={report} strategy={strategy} period={period} profiles={profiles} live={live && !imported} />}
      </div>}
    </main>
  </>;
}

createRoot(document.getElementById("root")).render(<App />);
