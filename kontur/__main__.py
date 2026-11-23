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

from .profiles import PROFILES, STRATEGIES, configuration

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
WEB_BUNDLE = WEB / "dist" / "app.js"
BUILD_FRONTEND = "Соберите интерфейс командами npm ci и npm run build из корня проекта."
DEFAULT_SYMBOLS = ("SBER", "GAZP", "LKOH", "TATN")
DEFAULT_LOTS = {"SBER": 10, "SBERP": 10, "GAZP": 10, "LKOH": 1, "TATN": 1, "YDEX": 1}


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
    if not WEB_BUNDLE.is_file():
        raise ValueError(BUILD_FRONTEND)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "styles.css").read_text(encoding="utf-8")
    app = WEB_BUNDLE.read_text(encoding="utf-8").replace("</script", "<\\/script")
    payload = report.read_text(encoding="utf-8").replace("<", "\\u003c")
    assets = {
        '<link rel="stylesheet" href="/styles.css">': f"<style>{css}</style>",
        '<script src="/app.js" defer></script>': "",
        '<div id="root"></div>': (
            f'<div id="root"></div>\n<script id="embedded-report" type="application/json">'
            f'{payload}</script>\n<script>{app}</script>'
        ),
    }
    for marker, content in assets.items():
        if marker not in html:
            raise RuntimeError(f"Не найден ресурс автономной панели: {marker}")
        html = html.replace(marker, content, 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")


def export_dashboard(args):
    standalone_dashboard(args.output, args.report)
    print(f"Панель: {args.output}")


def run_backtest(args):
    from .backtest import simulate
    from .horizons import experiment
    from .movers import movers
    from .contest import contest_scores
    from .data import load_bars, load_events, synthetic_data, write_bars, write_events
    from .model import EVENT_ENCODER, FEATURE_NAMES, MODEL_ALPHA, MODEL_NAME, build_features, execution_targets, latest_forecast, walk_forward

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
    overrides = {}
    for item in args.lot:
        symbol, separator, lot = item.partition("=")
        if not separator or symbol.upper() not in symbols or int(lot) <= 0:
            raise ValueError(f"Передайте размер лота в виде ТИКЕР=КОЛИЧЕСТВО: {item}")
        overrides[symbol.upper()] = int(lot)
    selected_preset, risk = configuration(args.profile, args.preset, lot_sizes, overrides)
    if missing := set(symbols) - risk.lot_sizes.keys():
        raise ValueError("Укажите --lot ТИКЕР=КОЛИЧЕСТВО для: " + ", ".join(sorted(missing)))
    features = build_features(bars, events)
    if selected_preset["objective"] == "exit":
        features = execution_targets(features, bars, risk)
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
    }
    agent = selected_preset["agent"]
    payload["preset"] = selected_preset
    payload["contest"] = contest_scores(strategies[agent]["metrics"])
    snapshot = load_bars(args.market_snapshot) if args.market_snapshot else bars
    if max(b.day for b in snapshot) > max(b.day for b in bars):
        raise ValueError("Рейтинг рынка датирован позже отчёта.")
    payload["movers"] = movers(snapshot)
    if args.market_snapshot:
        payload["meta"]["snapshot_sha256"] = input_hash(args.market_snapshot)
    if args.horizons:
        all_forecasts = walk_forward(features)
        payload["horizons"] = experiment(bars, all_forecasts.predictions, STRATEGIES[agent][1], risk)
    if args.holdout_after is not None and not args.study:
        raise ValueError("--holdout-after используется с --study.")
    if args.study:
        from .study import decade_study

        study, episodes = decade_study(bars, risk.lot_sizes, args.holdout_after)
        study["bars_sha256"] = payload["meta"]["bars_sha256"]
        payload["long_study"] = study
        write_json(output / "decade-study.json", study)
        with (output / "study-trades.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            fields = ("strategy", "period", "symbol", "entry_day", "exit_day", "quantity", "entry_price",
                      "exit_price", "gross_pnl", "commission", "slippage", "net_pnl", "reason")
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(episodes)
    write_json(output / "preset.json", selected_preset)
    report_path = output / "report.json"
    write_json(report_path, payload)
    write_json(output / "latest-signals.json", {
        "dataset": dataset,
        "purpose": "Прогнозы на дату последней свечи без проверки исполнения и результата.",
        "minimum_return_bps": max(risk.min_signal_bps, 2 * (risk.commission_bps + risk.slippage_bps)),
        "signals": latest_forecast(features),
    })
    export_simulation(output / "simulated-trades.csv", strategies)
    if WEB_BUNDLE.is_file():
        standalone_dashboard(output / "dashboard.html", report_path)
    else:
        print(BUILD_FRONTEND + " Готовый JSON можно открыть командой kontur dashboard после сборки.")
    label = "СИНТЕТИЧЕСКИЕ ДАННЫЕ" if demo else "ИСТОРИЧЕСКАЯ СИМУЛЯЦИЯ"
    print(f"{label} | {oos_start} — {oos_end} | {len(forecast.folds)} окон")
    for strategy in strategies.values():
        metrics = strategy["metrics"]
        print(f"{strategy['label']}: {metrics['net_pnl']:+.2f} ₽, "
              f"просадка {metrics['max_drawdown_pct']:.2f}%, сделок {metrics['trade_count']}")
    print(f"Отчёт: {report_path}")


def research_report(args):
    from .mishka import enabled
    from .research import ROLES, dossier, messages, review

    try:
        context = dossier(json.loads(args.report.read_text(encoding="utf-8")))
    except (KeyError, TypeError) as error:
        raise ValueError("Нужен корректный decade-study.json.") from error
    roles = list(ROLES) if args.role == "both" else [args.role]
    payload = {"dossier": context, "status": "prepared",
               "requests": {role: messages(context, role) for role in roles}}
    if args.send:
        if not enabled():
            raise ValueError("Задайте OPENROUTER_API_KEY и OPENROUTER_MODEL для отправки.")
        payload.update(status="requested", responses=review(context, roles))
    write_json(args.output, payload)
    print(f"Ресерч: {args.output}")
    if args.send and any(item["status"] != "answered" for item in payload["responses"].values()):
        raise RuntimeError("Один или несколько ответов OpenRouter недоступны. Сводка сохранена.")


def merge_history(args):
    from .data import load_bars, write_bars
    from .series import extend_with_iss

    if args.output.resolve() in {args.archive.resolve(), args.iss.resolve()}:
        raise ValueError("Выберите отдельный файл для объединённого архива.")
    archive, official = load_bars(args.archive), load_bars(args.iss)
    bars, checks = extend_with_iss(archive, official)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_bars(args.output, bars)
    write_json(args.output.with_suffix(".sources.json"), {**checks,
               "archive_sha256": input_hash(args.archive), "iss_sha256": input_hash(args.iss)})
    print(f"{checks['new_sessions']} новых сессий по {checks['end']} → {args.output}")


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
    def __init__(self, *args, report, mishka=False, **kwargs):
        self.mishka = mishka
        self.report = report
        super().__init__(*args, **kwargs)

    def send_json(self, status, data):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        from .mishka import SLOTS, answer

        if self.path not in ("/api/assistant", "/api/mishka") or not self.mishka:
            return self.send_json(404, {"error": "Ассистент отключён."})
        local = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
        host = self.headers.get("Host")
        if host not in local or self.headers.get("Origin") != "http://" + host:
            return self.send_json(403, {"error": "Откройте панель на локальном сервере."})
        if self.headers.get_content_type() != "application/json":
            return self.send_json(415, {"error": "Требуется JSON."})
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 4096:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError()
        except (UnicodeError, ValueError):
            return self.send_json(400, {"error": "Некорректный запрос."})
        if not SLOTS.acquire(blocking=False):
            return self.send_json(429, {"error": "Дождитесь ответа на предыдущий вопрос."})
        try:
            report = json.loads(self.report.read_text(encoding="utf-8"))
            message = answer(report, data.get("agent"), data.get("question"), data.get("period"))
        except ValueError as error:
            return self.send_json(400, {"error": str(error)})
        except RuntimeError as error:
            return self.send_json(502, {"error": str(error)})
        finally:
            SLOTS.release()
        self.send_json(200, {"answer": message})

    def do_GET(self):
        if self.path == "/api/capabilities":
            return self.send_json(200, {"assistant": self.mishka, "mishka": self.mishka})
        assets = {
            "/": (WEB / "index.html", "text/html"),
            "/index.html": (WEB / "index.html", "text/html"),
            "/styles.css": (WEB / "styles.css", "text/css"),
            "/app.js": (WEB_BUNDLE, "text/javascript"),
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
    from .mishka import enabled
    if not WEB_BUNDLE.is_file():
        raise ValueError(BUILD_FRONTEND)
    if args.mishka and (args.host != "127.0.0.1" or not enabled()):
        raise ValueError("Для ассистента нужны локальный адрес 127.0.0.1 и OPENROUTER_API_KEY.")
    handler = partial(ReportHandler, report=args.report.resolve(), mishka=args.mishka)
    with ThreadingHTTPServer((args.host, args.port), handler) as server:
        print(f"Циклон: http://{args.host}:{server.server_port}", flush=True)
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
    historical.add_argument("--dataset", choices=["moex", "archive", "csv"], default="csv")
    for command in (demo, historical):
        command.add_argument("--output", type=Path, default=Path("results"))
        command.add_argument("--holdout-days", type=int, default=120)
        command.add_argument("--horizons", action="store_true", help="Независимые прогоны за 6, 12, 24 и 60 месяцев")
        command.add_argument("--study", action="store_true", help="Многодневный эксперимент до 10 лет и независимые годовые окна")
        command.add_argument("--holdout-after", type=date.fromisoformat, help="Отдельная симуляция после указанной даты")
        profiles = command.add_mutually_exclusive_group()
        profiles.add_argument("--profile", choices=PROFILES, default="baseline")
        profiles.add_argument("--preset", type=Path, help="Применить экспортированный JSON-профиль")
        command.add_argument("--market-snapshot", type=Path, help="Свечи широкого рынка для рейтинга")
        command.add_argument("--lot", action="append", default=[], metavar="ТИКЕР=КОЛИЧЕСТВО")
        command.set_defaults(action=run_backtest)
    research = commands.add_parser("research", help="Подготовить сводку и запустить двух рецензентов по запросу")
    research.add_argument("--report", type=Path, required=True, help="Файл decade-study.json")
    research.add_argument("--output", type=Path, default=Path("results/research.json"))
    research.add_argument("--role", choices=("both", "hypothesis", "risk"), default="both")
    research.add_argument("--send", action="store_true", help="Передать агрегаты в OpenRouter")
    research.set_defaults(action=research_report)
    merge = commands.add_parser("merge-iss", help="Сверить пересечение и добавить к архиву свежие свечи ISS")
    merge.add_argument("--archive", type=Path, required=True)
    merge.add_argument("--iss", type=Path, required=True)
    merge.add_argument("--output", type=Path, required=True)
    merge.set_defaults(action=merge_history)
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
    dashboard = commands.add_parser("dashboard", help="Собрать автономную панель из готового report.json")
    dashboard.add_argument("--report", type=Path, default=Path("results/report.json"))
    dashboard.add_argument("--output", type=Path, default=Path("results/dashboard.html"))
    dashboard.set_defaults(action=export_dashboard)
    server = commands.add_parser("serve", help="Открыть локальную панель результатов")
    server.add_argument("--report", type=Path, default=Path("results/report.json"))
    server.add_argument("--assistant", "--mishka", dest="mishka", action="store_true",
                        help="Включить ассистента Циклона через OpenRouter на локальном сервере")
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
        print(f"Циклон: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
