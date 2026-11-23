export const KonturChart = (() => {
  "use strict";

  const DAY = 86400000;
  const COLORS = {
    ink: "#63716b",
    grid: "#e9eeea",
    baseline: "#b2beb6",
    crosshair: "#86988e",
    gain: "#25866b",
    loss: "#bc655d",
    fallback: "#25866b",
  };
  const axisNumber = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });
  const moneyNumber = new Intl.NumberFormat("ru-RU", {
    style: "currency", currency: "RUB", maximumFractionDigits: 2,
  });
  const signedMoney = new Intl.NumberFormat("ru-RU", {
    style: "currency", currency: "RUB", maximumFractionDigits: 2, signDisplay: "exceptZero",
  });
  const longDate = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
  const shortDate = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "short", timeZone: "UTC",
  });
  const monthDate = new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "UTC" });
  const chartLabels = {
    equity: "Капитал, рубли",
    drawdown: "Просадка от предыдущего пика, проценты",
    outcomes: "Чистый результат по торговым дням, рубли",
  };

  function timeOf(value) {
    const day = typeof value === "string" ? value.slice(0, 10) : "";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return NaN;
    const time = Date.parse(`${day}T00:00:00Z`);
    return Number.isFinite(time) && isoDay(time) === day ? time : NaN;
  }

  function isoDay(time) {
    return new Date(time).toISOString().slice(0, 10);
  }

  function valueLabel(value, metric) {
    if (!Number.isFinite(value)) return "—";
    if (metric === "drawdown") return `${axisNumber.format(value || 0)} %`;
    return (metric === "outcomes" ? signedMoney : moneyNumber).format(value);
  }

  function axisLabel(value, metric) {
    return `${axisNumber.format(Math.abs(value) < 1e-9 ? 0 : value)} ${metric === "drawdown" ? "%" : "₽"}`;
  }

  function niceStep(span, targetCount) {
    const wanted = span / Math.max(targetCount - 1, 1);
    const magnitude = 10 ** Math.floor(Math.log10(wanted));
    const unit = wanted / magnitude;
    return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10) * magnitude;
  }

  function scaleValues(values, metric, firstCapital, tickCount) {
    let min = Infinity;
    let max = -Infinity;
    for (const value of values) {
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
    if (metric === "equity") {
      const spread = Math.max(max - min, Math.max(Math.abs(max), 1) * 0.0015);
      const room = spread * 0.12;
      min -= room;
      max += room;
      if (Number.isFinite(firstCapital) && firstCapital >= min - room && firstCapital <= max + room) {
        min = Math.min(min, firstCapital);
        max = Math.max(max, firstCapital);
      }
    } else if (metric === "drawdown") {
      min = Math.min(-0.2, min * 1.08);
      max = 0;
    } else {
      const spread = Math.max(max - min, Math.abs(max), Math.abs(min), 10);
      min = min < 0 ? min - spread * 0.10 : 0;
      max = max > 0 ? max + spread * 0.10 : 0;
      if (min === max) max = 10;
    }
    const step = niceStep(max - min, tickCount);
    const low = Math.floor(min / step + 1e-9);
    const high = Math.ceil(max / step - 1e-9);
    const ticks = Array.from({ length: high - low + 1 }, (_, i) => (low + i) * step);
    return { min: low * step, max: high * step, ticks };
  }

  function dateTicks(min, max, width) {
    const span = (max - min) / DAY;
    if (span === 0) return [{ time: min, label: shortDate.format(min) }];
    const count = Math.max(2, Math.floor(width / 88));
    const ticks = [];
    if (span <= 85) {
      const step = [1, 2, 3, 5, 7, 14, 21, 28, 42].find((item) => item >= span / count) || 42;
      for (let time = Math.ceil(min / (step * DAY)) * step * DAY; time <= max; time += step * DAY) {
        ticks.push({ time, label: shortDate.format(time) });
      }
    } else {
      const step = [1, 2, 3, 4, 6, 12, 24, 36].find((item) => item >= span / (count * 30.4)) || 48;
      const first = new Date(min);
      const monthIndex = first.getUTCFullYear() * 12 + first.getUTCMonth();
      for (let index = Math.floor(monthIndex / step) * step; ; index += step) {
        const year = Math.floor(index / 12);
        const month = index % 12;
        const time = Date.UTC(year, month, 1);
        if (time > max) break;
        if (time < min) continue;
        const name = monthDate.format(time).replace(/\.$/, "");
        const label = span > 315 ? `${name} ’${String(year).slice(-2)}` : name;
        ticks.push({ time, label });
      }
    }
    return ticks.length > 1 ? ticks : [min, max].map((time) => ({ time, label: shortDate.format(time) }));
  }

  function nearest(values, desired) {
    let low = 0;
    let high = values.length - 1;
    while (low < high) {
      const middle = (low + high) >> 1;
      if (values[middle] < desired) low = middle + 1;
      else high = middle;
    }
    return low > 0 && desired - values[low - 1] <= values[low] - desired ? low - 1 : low;
  }

  function cleanColor(color) {
    return typeof color === "string" && CSS.supports("color", color) ? color : COLORS.fallback;
  }

  function element(tag, text) {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }

  class KonturChart {
    constructor(canvas, tooltipElement, liveElement, onDayChoose) {
      this.canvas = canvas;
      this.tooltip = tooltipElement;
      this.live = liveElement;
      this.onDayChoose = onDayChoose;
      this.context = canvas.getContext("2d");
      this.series = [];
      this.dayTimes = [];
      this.motion = window.matchMedia("(prefers-reduced-motion: reduce)");
      this.onResize = new ResizeObserver(() => this.requestDraw());
      this.onResize.observe(canvas);
      this.events = new AbortController();
      const options = { signal: this.events.signal };
      canvas.addEventListener("pointermove", (event) => this.hover(event), options);
      canvas.addEventListener("pointerleave", () => this.setActive(null), options);
      canvas.addEventListener("click", (event) => {
        const time = this.timeAtPointer(event);
        if (time != null) this.choose(time);
      }, options);
      canvas.addEventListener("keydown", (event) => this.keyDown(event), options);
      canvas.addEventListener("blur", () => this.setActive(null), options);
      if (!canvas.hasAttribute("tabindex")) canvas.tabIndex = 0;
    }

    update({ strategies = [], selected, metric = "equity", days = 0, trades = [], firstCapital }) {
      const previous = this.activeTime;
      const previousView = this.view;
      this.metric = Object.hasOwn(chartLabels, metric) ? metric : "equity";
      this.selected = selected;
      this.firstCapital = firstCapital;
      this.canvas.setAttribute("aria-label", chartLabels[this.metric]);
      this.tooltip.hidden = true;
      this.tooltipTime = null;
      const series = [];
      const allTimes = [];
      for (const strategy of strategies) {
        const rows = new Map();
        for (const datum of strategy.equity || []) {
          if (!datum) continue;
          const time = timeOf(datum.date);
          if (!Number.isFinite(time)) continue;
          allTimes.push(time);
          const value = this.metric === "drawdown" ? datum.drawdown_pct : datum.equity;
          if (Number.isFinite(value)) rows.set(time, this.metric === "drawdown" ? -Math.abs(value) : value);
        }
        series.push({
          key: strategy.key,
          label: strategy.label || strategy.key,
          color: cleanColor(strategy.color),
          rows,
        });
      }
      this.outcomes = new Map();
      if (this.metric === "outcomes") {
        for (const trade of trades) {
          const time = timeOf(trade?.trade_day || trade?.entry_at);
          if (!Number.isFinite(time) || !Number.isFinite(trade.pnl)) continue;
          allTimes.push(time);
          const day = this.outcomes.get(time) || { value: 0, count: 0 };
          day.value += trade.pnl;
          day.count++;
          this.outcomes.set(time, day);
        }
      }
      let start = Infinity;
      let end = -Infinity;
      for (const time of allTimes) {
        start = Math.min(start, time);
        end = Math.max(end, time);
      }
      if (days > 0) start = Math.max(start, end - (days - 1) * DAY);
      this.start = start;
      this.end = end;
      this.series = series.map((item) => ({
        ...item,
        points: [...item.rows].filter(([time]) => time >= start).sort(([a], [b]) => a - b),
      })).filter((item) => item.points.length);
      this.primary = this.series.find((item) => item.key === selected) || this.series[0];
      if (this.metric === "outcomes") {
        this.outcomes = new Map([...this.outcomes].filter(([time]) => time >= start).sort(([a], [b]) => a - b));
        this.dayTimes = [...this.outcomes.keys()];
      } else {
        this.dayTimes = [...new Set(this.series.flatMap((item) => item.points.map(([time]) => time)))].sort((a, b) => a - b);
      }
      this.activeTime = this.dayTimes.includes(previous) ? previous : null;
      this.view = JSON.stringify([
        this.metric, selected, start, end, firstCapital,
        this.metric === "outcomes" ? [...this.outcomes] : this.series.map(({ key, label, color, points }) => [key, label, color, points]),
      ]);
      if (this.live) this.live.textContent = "";
      this.geometry = null;
      if (this.view !== previousView) {
        this.startedAt = !this.motion.matches && this.dayTimes.length > 1 ? performance.now() : 0;
      }
      this.requestDraw();
    }

    destroy() {
      this.events.abort();
      this.onResize.disconnect();
      cancelAnimationFrame(this.frame || 0);
      this.tooltip.hidden = true;
    }

    requestDraw() {
      if (this.frame) return;
      this.frame = requestAnimationFrame((now) => {
        this.frame = 0;
        this.draw(now);
      });
    }

    timeAtPointer(event) {
      if (!this.geometry || !this.dayTimes.length) return null;
      const bounds = this.canvas.getBoundingClientRect();
      const x = event.clientX - bounds.left;
      const y = event.clientY - bounds.top;
      const g = this.geometry;
      if (x < g.left - 6 || x > g.right + 6 || y < g.top - 6 || y > g.bottom + 6) return null;
      const time = this.start + (x - g.left) * (this.end - this.start) / (g.right - g.left);
      return this.dayTimes[nearest(this.dayTimes, time)];
    }

    setActive(time, speak = false) {
      this.startedAt = 0;
      if (time !== this.activeTime) {
        this.activeTime = time;
        if (time == null) this.tooltip.hidden = true;
      }
      if (speak && time != null && this.live) {
        const value = this.metric === "outcomes" ? this.outcomes.get(time)?.value : this.primary?.rows.get(time);
        this.live.textContent = `${longDate.format(time)}, ${valueLabel(value, this.metric)}`;
      }
      this.requestDraw();
    }

    hover(event) {
      if (event.pointerType !== "touch") this.setActive(this.timeAtPointer(event));
    }

    choose(time) {
      this.setActive(time, true);
      if (this.onDayChoose) this.onDayChoose(isoDay(time));
    }

    keyDown(event) {
      if (!this.dayTimes.length) return;
      const final = this.dayTimes.length - 1;
      let index = this.dayTimes.indexOf(this.activeTime);
      if (event.key === "ArrowLeft") index = index < 0 ? final : Math.max(0, index - 1);
      else if (event.key === "ArrowRight") index = Math.min(final, index + 1);
      else if (event.key === "Home") index = 0;
      else if (event.key === "End") index = final;
      else if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        this.choose(this.dayTimes[index < 0 ? final : index]);
        return;
      } else if (event.key === "Escape") {
        this.setActive(null);
        return;
      } else return;
      event.preventDefault();
      this.setActive(this.dayTimes[index], true);
    }

    draw(now) {
      const ctx = this.context;
      if (!ctx) return;
      const bounds = this.canvas.getBoundingClientRect();
      const width = bounds.width;
      const height = bounds.height;
      if (width < 120 || height < 80) {
        this.geometry = null;
        return;
      }
      const ratio = Math.min(window.devicePixelRatio || 1, 3);
      const pixelWidth = Math.round(width * ratio);
      const pixelHeight = Math.round(height * ratio);
      if (this.canvas.width !== pixelWidth) this.canvas.width = pixelWidth;
      if (this.canvas.height !== pixelHeight) this.canvas.height = pixelHeight;
      ctx.setTransform(pixelWidth / width, 0, 0, pixelHeight / height, 0, 0);
      ctx.clearRect(0, 0, width, height);
      ctx.font = "11px Inter, system-ui, sans-serif";
      ctx.textBaseline = "middle";
      ctx.lineWidth = 1;
      if (!this.dayTimes.length) {
        ctx.fillStyle = COLORS.ink;
        ctx.textAlign = "center";
        ctx.fillText(this.metric === "outcomes" ? "За этот период нет сделок" : "За этот период нет данных", width / 2, height / 2);
        return;
      }
      const values = this.metric === "outcomes"
        ? [...this.outcomes.values()].map((day) => day.value)
        : this.series.flatMap((item) => item.points.map(([, value]) => value));
      const scale = scaleValues(values, this.metric, this.firstCapital, height < 230 ? 5 : 6);
      const labelWidth = Math.max(...scale.ticks.map((value) => ctx.measureText(axisLabel(value, this.metric)).width));
      const left = labelWidth + 13;
      const right = width - 13;
      const top = 15;
      const bottom = height - 28;
      const x = (time) => this.end === this.start ? (left + right) / 2 : left + (time - this.start) * (right - left) / (this.end - this.start);
      const y = (value) => bottom - (value - scale.min) * (bottom - top) / (scale.max - scale.min);
      const g = { top, left, right, bottom, x, y };
      this.geometry = g;
      this.drawAxes(ctx, g, scale, width);
      const elapsed = this.startedAt && !this.motion.matches ? now - this.startedAt : 300;
      const fraction = Math.min(1, Math.max(0, elapsed / 275));
      const reveal = 1 - (1 - fraction) ** 3;
      ctx.save();
      ctx.beginPath();
      ctx.rect(left - 8, top - 6, (right - left) * reveal + 16, bottom - top + 12);
      ctx.clip();
      if (this.metric === "outcomes") this.drawOutcomes(ctx, g);
      else this.drawLines(ctx, g);
      ctx.restore();
      if (this.activeTime != null && fraction === 1) this.drawPointer(ctx, g, width);
      if (fraction < 1) this.requestDraw();
    }

    drawAxes(ctx, g, scale, width) {
      const { left, right, bottom, x, y } = g;
      ctx.textAlign = "right";
      ctx.fillStyle = COLORS.ink;
      for (const value of scale.ticks) {
        const level = y(value);
        const isZero = value === 0 && this.metric !== "equity";
        ctx.strokeStyle = isZero ? COLORS.baseline : COLORS.grid;
        ctx.beginPath();
        ctx.moveTo(left, level);
        ctx.lineTo(right, level);
        ctx.stroke();
        ctx.fillText(axisLabel(value, this.metric), left - 9, level);
      }
      if (this.metric === "equity" && this.firstCapital >= scale.min && this.firstCapital <= scale.max) {
        ctx.save();
        ctx.strokeStyle = COLORS.baseline;
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        ctx.moveTo(left, y(this.firstCapital));
        ctx.lineTo(right, y(this.firstCapital));
        ctx.stroke();
        ctx.restore();
      }
      ctx.textAlign = "center";
      let previousRight = -Infinity;
      for (const tick of dateTicks(this.start, this.end, right - left)) {
        const half = ctx.measureText(tick.label).width / 2;
        const center = Math.max(half + 4, Math.min(width - half - 3, x(tick.time)));
        if (center - half < previousRight + 11) continue;
        ctx.fillText(tick.label, center, bottom + 17);
        previousRight = center + half;
      }
    }

    drawLines(ctx, g) {
      const plotted = [...this.series.filter((item) => item !== this.primary), this.primary];
      for (const item of plotted) {
        const { points, color } = item;
        const primary = item === this.primary;
        if (primary && points.length > 1) {
          const shade = ctx.createLinearGradient(0, g.top, 0, g.bottom);
          shade.addColorStop(0, color);
          shade.addColorStop(1, "transparent");
          ctx.save();
          ctx.fillStyle = shade;
          ctx.globalAlpha = 0.12;
          const floor = this.metric === "drawdown" ? g.y(0) : g.bottom;
          ctx.beginPath();
          ctx.moveTo(g.x(points[0][0]), floor);
          for (const [time, value] of points) ctx.lineTo(g.x(time), g.y(value));
          ctx.lineTo(g.x(points[points.length - 1][0]), floor);
          ctx.closePath();
          ctx.fill();
          ctx.restore();
        }
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = primary ? 2.25 : 1.5;
        ctx.globalAlpha = primary ? 1 : 0.76;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        ctx.beginPath();
        for (const [i, [time, value]] of points.entries()) {
          if (i) ctx.lineTo(g.x(time), g.y(value));
          else ctx.moveTo(g.x(time), g.y(value));
        }
        ctx.stroke();
        ctx.restore();
        if (primary || points.length === 1) {
          const [lastTime, lastValue] = points[points.length - 1];
          this.dot(ctx, g.x(lastTime), g.y(lastValue), color, primary ? 3.1 : 2.5);
        }
      }
    }

    drawOutcomes(ctx, g) {
      const zero = g.y(0);
      const width = this.end === this.start ? 16 : Math.max(1.0, Math.min(17, (g.right - g.left) * DAY * 0.78 / (this.end - this.start)));
      for (const [time, day] of this.outcomes) {
        const y = g.y(day.value);
        ctx.fillStyle = day.value >= 0 ? COLORS.gain : COLORS.loss;
        ctx.globalAlpha = this.activeTime == null || this.activeTime === time ? 0.9 : 0.48;
        ctx.fillRect(g.x(time) - width / 2, Math.min(y, zero), width, Math.max(Math.abs(y - zero), 1));
      }
      ctx.globalAlpha = 1;
    }

    dot(ctx, x, y, color, radius) {
      ctx.beginPath();
      ctx.fillStyle = color;
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }

    drawPointer(ctx, g, width) {
      const time = this.activeTime;
      const x = g.x(time);
      ctx.save();
      ctx.strokeStyle = COLORS.crosshair;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(x, g.top);
      ctx.lineTo(x, g.bottom);
      ctx.stroke();
      ctx.restore();
      if (this.metric !== "outcomes") {
        for (const item of this.series) {
          if (item.rows.has(time)) this.dot(ctx, x, g.y(item.rows.get(time)), item.color, item === this.primary ? 4.1 : 3.3);
        }
      }
      if (this.tooltipTime !== time) {
        const rows = [element("strong", longDate.format(time))];
        if (this.metric === "outcomes") {
          const day = this.outcomes.get(time);
          const result = element("div", "");
          result.append(element("span", "Чистый результат"), element("b", valueLabel(day.value, this.metric)));
          const count = element("div", "");
          count.append(element("span", "Сделки"), element("b", String(day.count)));
          rows.push(result, count);
        } else {
          for (const item of [this.primary, ...this.series.filter((candidate) => candidate !== this.primary)]) {
            const row = element("div", "");
            const label = element("span", item.label);
            label.style.borderInlineStart = `3px solid ${item.color}`;
            label.style.paddingInlineStart = "6px";
            row.append(label, element("b", valueLabel(item.rows.get(time), this.metric)));
            rows.push(row);
          }
        }
        this.tooltip.replaceChildren(...rows);
        this.tooltipTime = time;
      }
      this.tooltip.hidden = false;
      const tipWidth = this.tooltip.offsetWidth;
      const left = x + 14 + tipWidth <= width - 5 ? x + 14 : x - tipWidth - 14;
      this.tooltip.style.left = `${Math.max(4, Math.min(width - tipWidth - 4, left))}px`;
      this.tooltip.style.top = `${g.top + 5}px`;
    }
  }

  return KonturChart;
})();
