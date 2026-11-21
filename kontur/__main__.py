"""Рабочие команды для данных, модели, отчёта и журнала исполнений."""

import argparse
import csv
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from functools import partial
from hashlib import file_digest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DEFAULT_SYMBOLS = ("SBER", "GAZP", "LKOH", "TATN")
DEFAULT_LOTS = {"SBER": 10, "SBERP": 10, "GAZP": 10, "LKOH": 1, "TATN": 1, "YDEX": 1}
STRATEGIES = {
    "hybrid": ("События + рынок", "event_return"),
    "market": ("Только рынок", "market_return"),
    "momentum": ("Импульс цены", "momentum_return"),
}


def json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Неподдерживаемое значение: {type(value).__name__}")


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, default=json_value), encoding="utf-8")


def input_hash(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def display_trade(trade):
    return {
        **trade,
        "symbol": trade["ticker"],
        "signal_date": trade["signal_day"],
        "pnl": trade["net_pnl"],
        "forecast_pct": trade["prediction"] * 100,
    }


def display_event(event):
    return {
        "id": event.event_id,
        "symbol": event.symbol,
        "published_at": event.published_at,
        "available_at": event.available_at,
        "source": event.source,
        "author": event.author,
        "title": event.title,
        "url": event.url,
    }


def export_simulation(path, strategies):
    fields = (
        "strategy", "id", "symbol", "signal_at", "entry_at", "exit_at", "trade_day",
        "quantity", "entry_price", "exit_price", "commission", "slippage", "net_pnl",
        "prediction", "reason", "fold",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for name, strategy in strategies.items():
            writer.writerows({**trade, "strategy": name} for trade in strategy["trades"])


def standalone_dashboard(output, report):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    charts = (WEB / "charts.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    app = (WEB / "app.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    payload = report.read_text(encoding="utf-8").replace("<", "\\u003c")
    assets = {
        '<link rel="stylesheet" href="/styles.css" />': f"<style>{css}</style>",
        '<script src="/charts.js" defer></script>': f"<script>{charts}</script>",
        '<script type="module" src="/app.js"></script>': (
            f'<script id="embedded-report" type="application/json">{payload}</script>\n<script type="module">{app}</script>'
        ),
    }
    for marker, content in assets.items():
        if marker not in html:
            raise RuntimeError(f"Не найден ресурс автономной панели: {marker}")
        html = html.replace(marker, content, 1)
    output.write_text(html, encoding="utf-8")


def run_backtest(args):
    from .backtest import RiskConfig, simulate
    from .contest import contest_scores
    from .data import load_bars, load_events, synthetic_data, write_bars, write_events
    from .model import EVENT_ENCODER, FEATURE_NAMES, MODEL_ALPHA, MODEL_NAME, build_features, latest_forecast, walk_forward

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    demo = args.command == "demo"
    if demo:
        bars, events = synthetic_data(seed=args.seed, sessions=args.sessions)
        bars_path, events_path = output / "demo-bars.csv", output / "demo-events.csv"
        write_bars(bars_path, bars)
        write_events(events_path, events)
        dataset = "synthetic"
    else:
        bars_path, events_path = args.bars, args.events
        bars = load_bars(bars_path)
        events = load_events(events_path) if events_path else []
        dataset = args.dataset
    if not bars:
        raise ValueError("В наборе нет свечей.")
    symbols = sorted({bar.symbol for bar in bars})
    lot_sizes = {symbol: DEFAULT_LOTS[symbol] for symbol in symbols if symbol in DEFAULT_LOTS}
    for item in args.lot:
        symbol, separator, lot = item.partition("=")
        if not separator or symbol.upper() not in symbols or int(lot) <= 0:
            raise ValueError(f"Передайте размер лота в виде ТИКЕР=КОЛИЧЕСТВО: {item}")
        lot_sizes[symbol.upper()] = int(lot)
    if missing := set(symbols) - lot_sizes.keys():
        raise ValueError("Укажите --lot ТИКЕР=КОЛИЧЕСТВО для: " + ", ".join(sorted(missing)))
    risk = RiskConfig(lot_sizes=lot_sizes)
    features = build_features(bars, events)
    forecast = walk_forward(features, holdout_days=args.holdout_days)
    if not forecast.predictions:
        raise ValueError("Недостаточно истории для проверки на отложенном периоде.")
    strategies = {}
    for key, (label, field) in STRATEGIES.items():
        strategy = simulate(bars, forecast.predictions, field, risk)
        trades = [display_trade(t if key == "hybrid" else {**t, "evidence": []}) for t in strategy["trades"]]
        strategies[key] = {**strategy, "label": label, "trades": trades}
    oos_start = min(p.trade_day for p in forecast.predictions)
    oos_end = max(p.trade_day for p in forecast.predictions)
    latest = sorted(events, key=lambda event: event.available_at, reverse=True)[:80]
    payload = {
        "schema_version": 1,
        "meta": {
            "dataset": dataset,
            "sample_start": min(b.day for b in bars),
            "sample_end": max(b.day for b in bars),
            "oos_start": oos_start,
            "oos_end": oos_end,
            "symbols": symbols,
            "start_capital": risk.initial_capital,
            "seed": args.seed if demo else None,
            "bars": len(bars),
            "events": len(events),
            "event_source": "Синтетический сценарий" if demo else ("Импорт событий" if events else "События не загружены"),
            "commission_bps": risk.commission_bps,
            "slippage_bps": risk.slippage_bps,
            "folds": len(forecast.folds),
            "bars_sha256": input_hash(bars_path),
            "events_sha256": input_hash(events_path) if events_path else None,
            "model": MODEL_NAME,
            "regularization": MODEL_ALPHA,
            "event_encoder": EVENT_ENCODER,
            "features": FEATURE_NAMES,
            "notes": [
                "Обучение только на доступной истории. Прогноз формируется после закрытия, вход — на следующем открытии.",
                "Комиссия и проскальзывание учтены на каждой стороне сделки.",
                "Историческая симуляция не засчитывается как реальная торговля в Cassandra.",
            ],
        },
        "strategies": strategies,
        "events": [display_event(event) for event in latest],
        "diagnostics": {**forecast.diagnostics, "fold_details": forecast.folds},
        "contest": contest_scores(strategies["hybrid"]["metrics"]),
    }
    report_path = output / "report.json"
    write_json(report_path, payload)
    write_json(output / "latest-signals.json", {
        "dataset": dataset,
        "purpose": "Прогнозы на дату последней свечи без проверки исполнения и результата.",
        "minimum_return_bps": max(risk.min_signal_bps, 2 * (risk.commission_bps + risk.slippage_bps)),
        "signals": latest_forecast(features),
    })
    export_simulation(output / "simulated-trades.csv", strategies)
    standalone_dashboard(output / "dashboard.html", report_path)
    label = "СИНТЕТИЧЕСКИЕ ДАННЫЕ" if demo else "ИСТОРИЧЕСКАЯ СИМУЛЯЦИЯ"
    print(f"{label} | {oos_start} — {oos_end} | {len(forecast.folds)} окон")
    for strategy in strategies.values():
        metrics = strategy["metrics"]
        print(f"{strategy['label']}: {metrics['net_pnl']:+.2f} ₽, "
              f"просадка {metrics['max_drawdown_pct']:.2f}%, сделок {metrics['trade_count']}")
    print(f"Отчёт: {report_path}")


def pull_history(args):
    from .data import download_moex, write_bars

    bars = download_moex(args.symbols, args.start, args.end, open_time=args.open_time, close_time=args.close_time)
    if not bars:
        raise ValueError("MOEX ISS не вернул свечей за указанный период.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_bars(args.output, bars)
    print(f"{len(bars)} свечей MOEX ISS → {args.output}")


def collect_news(args):
    from .feeds import collect_feeds

    added = collect_feeds(args.config, args.output)
    print(f"Новых событий: {added} → {args.output}")


def audit(args):
    from .contest import audit_executions, contest_scores

    report = audit_executions(args.fills, args.equity, team_id=args.team_id)
    metrics = report["metrics"]
    report["contest"] = contest_scores(
        metrics, actual_situations=metrics["trade_count"], actual_days=metrics["trade_days"],
        validated_holdout_situations=args.holdout_situations, best_positive_return_pct=args.best_return,
    )
    write_json(args.output, report)
    print(f"Аудит исполнений: {args.output}")


class ReportHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, report, **kwargs):
        self.report = report
        super().__init__(*args, **kwargs)

    def do_GET(self):
        assets = {
            "/": (WEB / "index.html", "text/html"),
            "/index.html": (WEB / "index.html", "text/html"),
            "/styles.css": (WEB / "styles.css", "text/css"),
            "/charts.js": (WEB / "charts.js", "text/javascript"),
            "/app.js": (WEB / "app.js", "text/javascript"),
            "/results/report.json": (self.report, "application/json"),
            "/results/simulated-trades.csv": (self.report.with_name("simulated-trades.csv"), "text/csv"),
        }
        asset = assets.get(urlsplit(self.path).path)
        if asset is None or not asset[0].is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        path, mime = asset
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


def serve(args):
    handler = partial(ReportHandler, report=args.report.resolve())
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(f"Контур: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def parser():
    main = argparse.ArgumentParser(prog="python -m kontur", description=__doc__)
    commands = main.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Проверить конвейер на синтетических данных")
    demo.add_argument("--seed", type=int, default=21)
    demo.add_argument("--sessions", type=int, default=550)
    historical = commands.add_parser("run", help="Обучить модели и провести историческую проверку")
    historical.add_argument("--bars", type=Path, required=True)
    historical.add_argument("--events", type=Path)
    historical.add_argument("--dataset", choices=["moex", "csv"], default="csv")
    for command in (demo, historical):
        command.add_argument("--output", type=Path, default=Path("results"))
        command.add_argument("--holdout-days", type=int, default=120)
        command.add_argument("--lot", action="append", default=[], metavar="ТИКЕР=КОЛИЧЕСТВО")
        command.set_defaults(action=run_backtest)
    download = commands.add_parser("download", help="Скачать дневные свечи MOEX ISS")
    download.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    download.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    download.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    download.add_argument("--open-time", type=time.fromisoformat, default=time(10), help="Условное открытие, МСК")
    download.add_argument("--close-time", type=time.fromisoformat, default=time(23, 59, 59), help="Отсечка дневных данных, МСК")
    download.add_argument("--output", type=Path, default=Path("data/moex.csv"))
    download.set_defaults(action=pull_history)
    collect = commands.add_parser("collect", help="Собрать события из настроенных публичных лент RSS/Atom")
    collect.add_argument("--config", type=Path, required=True)
    collect.add_argument("--output", type=Path, default=Path("data/events.csv"))
    collect.set_defaults(action=collect_news)
    server = commands.add_parser("serve", help="Открыть локальную панель результатов")
    server.add_argument("--report", type=Path, default=Path("results/report.json"))
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    server.set_defaults(action=serve)
    review = commands.add_parser("audit", help="Проверить нормализованный CSV реальных исполнений")
    review.add_argument("--fills", type=Path, required=True)
    review.add_argument("--equity", type=Path, required=True)
    review.add_argument("--team-id")
    review.add_argument("--holdout-situations", type=int, default=0)
    review.add_argument("--best-return", type=float)
    review.add_argument("--output", type=Path, default=Path("results/execution-audit.json"))
    review.set_defaults(action=audit)
    return main


def main():
    args = parser().parse_args()
    try:
        args.action(args)
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Контур: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
