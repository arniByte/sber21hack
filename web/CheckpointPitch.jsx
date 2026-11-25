import React, { useEffect, useRef, useState } from "react";
import OrbScene from "./OrbScene.jsx";
import { date, dates, factor, num, pct, rub, tone } from "./report.js";

const reason = { cutoff: "Отсечка", target: "Цель", stop: "Стоп", gap: "Гэп через стоп" };
const hour = (h) => h + ":00";
const exitLabel = (trade) => trade?.exit_at ? hour(trade.exit_hour_msk) : "свеча " + hour(trade.exit_hour_msk);

function CandlePlot({ sample, step }) {
  const canvas = useRef(null);
  useEffect(() => {
    const node = canvas.current;
    const bars = sample.candles.filter((b) => b.hour <= step.until);
    const band = bars[0];
    const draw = () => {
      const { width, height } = node.getBoundingClientRect();
      const ratio = Math.min(devicePixelRatio || 1, 2);
      if (!width || !height) return;
      node.width = Math.round(width * ratio);
      node.height = Math.round(height * ratio);
      const ctx = node.getContext("2d");
      if (!ctx) return;
      ctx.scale(ratio, ratio);
      const levels = step.order ? [
        [sample.limit, "#425caa", "Лимит"],
        [sample.stop, "#a44049", "Стоп"],
        [sample.target, "#228373", "Цель"],
      ] : [[band.high, "#776392", "Верх"], [band.low, "#776392", "Низ"]];
      const values = bars.flatMap((b) => [b.high, b.low]).concat(levels.map(([p]) => p));
      const spread = Math.max(...values) - Math.min(...values) || 1;
      const low = Math.min(...values) - spread * .14;
      const high = Math.max(...values) + spread * .14;
      const left = 11;
      const right = width - 65;
      const bottom = height - 33;
      const y = (v) => bottom - (v - low) / (high - low) * (bottom - 13);
      const dx = (right - left) / sample.candles.length;
      ctx.font = "11px system-ui";
      ctx.textBaseline = "middle";
      for (let i = 0; i < 4; i++) {
        const v = low + (high - low) * i / 3;
        const row = y(v);
        ctx.strokeStyle = "#e7e9e4";
        ctx.beginPath(); ctx.moveTo(left, row); ctx.lineTo(right, row); ctx.stroke();
        ctx.fillStyle = "#687063";
        ctx.fillText(num(v), right + 8, row);
      }
      ctx.fillStyle = "#b9a9d62e";
      ctx.fillRect(left, y(band.high), right - left, y(band.low) - y(band.high));
      for (const [value, color, label] of levels) {
        const row = y(value);
        ctx.strokeStyle = color;
        ctx.setLineDash([4, 4]);
        ctx.beginPath(); ctx.moveTo(left, row); ctx.lineTo(right, row); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillText(label, right + 8, row + 2);
      }
      bars.forEach((bar, index) => {
        const x = left + (index + .5) * dx;
        ctx.strokeStyle = ctx.fillStyle = bar.close >= bar.open ? "#328674" : "#b55d70";
        ctx.beginPath(); ctx.moveTo(x, y(bar.high)); ctx.lineTo(x, y(bar.low)); ctx.stroke();
        const w = Math.max(4, Math.min(15, dx * .52));
        ctx.fillRect(x - w / 2, Math.min(y(bar.open), y(bar.close)), w, Math.max(2, Math.abs(y(bar.open) - y(bar.close))));
        if (dx > 29 || index % 2 === 0) {
          ctx.fillStyle = "#687063";
          ctx.textAlign = "center";
          ctx.fillText(hour(bar.hour), x, bottom + 17);
          ctx.textAlign = "start";
        }
      });
    };
    const observer = new ResizeObserver(draw);
    observer.observe(node);
    draw();
    return () => observer.disconnect();
  }, [sample, step]);
  return <canvas className="jury-candles" ref={canvas} role="img"
    aria-label={sample.symbol + " · свечи по " + hour(step.until) + " МСК · " + step.label} />;
}

function demoSteps(sample) {
  const { trade, hour: placed, limit, stop, target, side } = sample;
  return [
    { label: "Диапазон", until: 10,
      text: "Фиксируем максимум и минимум свечи 10:00–11:00 МСК." },
    { label: "Пробой", until: placed - 2,
      text: "Час закрывается " + (side === 1 ? "выше верхней" : "ниже нижней") + " границы диапазона." },
    { label: "Ретест", until: placed - 1,
      text: "Следующая свеча тестирует уровень и закрывается со стороны пробоя." },
    { label: "Лимит", until: placed - 1, order: true,
      text: (side === 1 ? "Лонг" : "Шорт") + " от " + rub(limit) + ". Стоп " + rub(stop) + ", цель " + rub(target) + ". Заявка с " + hour(placed) + " до " + hour(placed + 1) + " МСК." },
    { label: "Итог", until: trade?.exit_hour_msk ?? placed, order: true,
      text: trade ? (reason[trade.reason] || "Выход") + " · " + exitLabel(trade) + " · " + rub(trade.net_pnl, true) + " после издержек."
        : "Цена не прошла лимит насквозь. Через час заявка снята. Позиция не открывалась." },
  ];
}

function Replay({ examples }) {
  const [example, setExample] = useState(0);
  const [index, setIndex] = useState(0);
  const sample = examples[example];
  if (!sample) return null;
  const steps = demoSteps(sample);
  const step = steps[index];
  return <section id="replay" className="surface secondary-page jury-block">
    <div className="section-head">
      <div><h2>Сделка по шагам</h2><p>{sample.symbol} · {date(sample.day)} · архив, МСК</p></div>
      <div className="segments" role="group" aria-label="Пример">
        {examples.map((e, i) => <button key={i} aria-pressed={example === i} onClick={() => {
          setExample(i); setIndex(0);
        }}>{e.trade ? "Исполнение" : "Отмена заявки"}</button>)}
      </div>
    </div>
    <CandlePlot sample={sample} step={step} />
    <div className="jury-step" aria-live="polite"><strong>{step.label}</strong><p>{step.text}</p></div>
    <div className="jury-controls">
      <div className="segments" role="group" aria-label="Шаг">
        {steps.map((s, i) => <button key={s.label} aria-pressed={index === i} onClick={() => setIndex(i)}>{s.label}</button>)}
      </div>
      <button className="secondary" onClick={() => setIndex(index === steps.length - 1 ? 0 : index + 1)}>
        {index === steps.length - 1 ? "С начала" : "Следующий шаг →"}
      </button>
    </div>
  </section>;
}

function Comparison({ report }) {
  const [late, setLate] = useState(false);
  const suffix = late ? "_holdout" : "";
  const v1 = report.strategies["checkpoint_retest" + suffix];
  const v2 = report.strategies["checkpoint_limit" + suffix];
  const rows = [
    ["Доходность", pct(v1.metrics.return_pct, true), pct(v2.metrics.return_pct, true)],
    ["Просадка", pct(v1.metrics.max_drawdown_pct), pct(v2.metrics.max_drawdown_pct)],
    ["Сделки", num(v1.metrics.trade_count), num(v2.metrics.trade_count)],
    ["Profit Factor", factor(v1.metrics), factor(v2.metrics)],
    ["Заявок исполнено", num(v1.metrics.trade_count) + " / " + num(v1.orders), num(v2.metrics.trade_count) + " / " + num(v2.orders)],
    ["Двойные издержки", ...[v1, v2].map((v) => v.stress_trades ? pct(v.stress_pct, true) : "0 сделок")],
  ];
  return <section id="evidence" className="surface secondary-page jury-block">
    <div className="section-head"><div><h2>Проверка на архиве</h2><p>{dates(v2.start, v2.end)} · {num(v2.sessions)} сессий</p></div>
      <div className="segments" role="group" aria-label="Проверочный период">
        <button aria-pressed={!late} onClick={() => setLate(false)}>Весь архив</button>
        <button aria-pressed={late} onClick={() => setLate(true)}>Июль — сентябрь</button>
      </div>
    </div>
    <table className="report-table"><thead><tr><th>Метрика</th><th>1.0 · Вход по открытию</th><th>2.0 · Лимит</th></tr></thead>
      <tbody>{rows.map(([label, a, b]) => <tr key={label}><th scope="row">{label}</th><td>{a}</td><td>{b}</td></tr>)}</tbody>
    </table>
    <p className="jury-note muted">Комиссия и проскальзывание по 5 б.п. на сторону. Часовой архив не показывает биржевую очередь. Поздний отрезок входит в полный архив.</p>
  </section>;
}

export default function CheckpointPitch({ report }) {
  useEffect(() => { document.title = "Циклон · Интрадей 2.0"; }, []);
  const main = report.strategies.checkpoint_limit;
  const archive = report.checkpoint;
  const overnight = main.trades.filter((t) => t.exit_hour_msk > archive.flat_at_msk || Boolean(t.exit_at && t.exit_at.slice(0, 10) !== t.trade_day)).length;
  return <main className="page jury-page">
    <header className="jury-header">
      <a className="brand" href="#top"><i aria-hidden="true" />Циклон</a>
      <nav aria-label="Слайды"><a href="#replay">Демо</a><a href="#evidence">Проверка</a><a href="#defense">Защита</a></nav>
    </header>
    <section id="top" className="hero">
      <OrbScene />
      <div className="hero-text">
        <span className="source"><i aria-hidden="true" />Часовой архив Мосбиржи</span>
        <h1>Интрадей 2.0</h1>
        <p>Пробой с ретестом и лимитной заявкой</p>
      </div>
    </section>
    <dl className="metrics main-metrics jury-metrics">
      <div><dt>Доходность после издержек</dt><dd className={tone(main.metrics.return_pct)}>{pct(main.metrics.return_pct, true)}</dd></div>
      <div><dt>Макс. просадка</dt><dd>{pct(main.metrics.max_drawdown_pct)}</dd></div>
      <div><dt>Переносы</dt><dd>{num(overnight)}</dd></div>
    </dl>
    <Replay examples={archive.examples} />
    <Comparison report={report} />
    <section id="defense" className="surface secondary-page jury-block">
      <h2>Как защищать</h2>
      <blockquote className="jury-quote">«После пробоя ждём ретест и выставляем лимит у уровня. За час цена должна пройти его насквозь. В симуляции исполнились {main.metrics.trade_count} заявки из {main.orders}. После издержек получили {pct(main.metrics.return_pct, true)}. Главная гипотеза для следующей проверки — исполнимость на более мелких свечах и при тарифах площадки».</blockquote>
      <details className="detail"><summary><span>Механика и риск</span><i aria-hidden="true" /></summary>
        <div className="detail-body"><p>Диапазон первого часа 10:00–11:00 МСК. Лонг после верхнего пробоя, шорт после нижнего. Стоп за свечой ретеста, цель 2R. Не более одной сделки в день, риск на модельном стопе до 0,30% капитала. В 18:00 МСК закрываем позицию.</p>
          <p>В свече исполнения цель не засчитывается. Если позже в одном баре задеты цель и стоп, порядок считаем по стопу первым. Плечо площадки и допуск к шортам требуют подтверждения.</p></div>
      </details>
      <details className="detail"><summary><span>Стек и тесты</span><i aria-hidden="true" /></summary>
        <div className="detail-body"><p>Python 3.12 рассчитывает сигналы, издержки и риск. Архивные CSV сверяются по SHA‑256. React рисует интерфейс, Canvas — свечи, Three.js — сферу. esbuild собирает скрипт и данные в HTML.</p>
          <p>Тесты покрывают исполнение лимита, порядок стопа и цели, комиссии в лонге и шорте и закрытие по отсечке. Демо показывает первую исполненную и первую снятую заявку в хронологическом порядке.</p></div>
      </details>
    </section>
  </main>;
}
