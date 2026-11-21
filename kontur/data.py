"""MOEX daily market data and timestamped event archives."""

from __future__ import annotations

import csv
import json
import math
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Iterator
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


MSK = ZoneInfo("Europe/Moscow")
DEMO_OPEN = time(10)
DEMO_CLOSE = time(18, 50)
ISS_CLOSE = time(23, 59, 59)
ISS_ROOT = "https://iss.moex.com/iss/history/engines/stock/markets/shares/boards/TQBR/securities"
ISS_COLUMNS = ("TRADEDATE", "SECID", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME")
BAR_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "open_time", "close_time")
EVENT_COLUMNS = ("event_id", "symbol", "published_at", "available_at", "source", "author", "title", "text", "url")
SYMBOL_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,19}")


def _symbol(value: str) -> str:
    result = value.strip().upper()
    if not SYMBOL_PATTERN.fullmatch(result):
        raise ValueError(f"недопустимый тикер {value!r}")
    return result


def _clock(value: str) -> time:
    result = time.fromisoformat(value)
    if result.tzinfo is not None:
        raise ValueError("укажите локальное время МСК без смещения")
    return result


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    open_time: time = DEMO_OPEN
    close_time: time = DEMO_CLOSE

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        amounts = (self.open, self.high, self.low, self.close)
        if any(not math.isfinite(value) or value <= 0 for value in amounts):
            raise ValueError("цены должны быть конечными и положительными")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("границы свечи не включают цены открытия и закрытия")
        if not math.isfinite(self.volume) or self.volume < 0:
            raise ValueError("объём должен быть конечным и неотрицательным")
        if self.open_time.tzinfo or self.close_time.tzinfo or self.open_time >= self.close_time:
            raise ValueError("открытие и закрытие задаются локальным временем МСК в пределах дня")

    @property
    def open_at(self) -> datetime:
        return datetime.combine(self.day, self.open_time, MSK)

    @property
    def close_at(self) -> datetime:
        return datetime.combine(self.day, self.close_time, MSK)


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    symbol: str
    published_at: datetime
    available_at: datetime
    source: str
    author: str = ""
    title: str = ""
    text: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        symbol = self.symbol.strip()
        object.__setattr__(self, "symbol", "*" if symbol == "*" else _symbol(symbol))
        if not self.event_id.strip() or not self.source.strip() or not self.title.strip():
            raise ValueError("событию нужны идентификатор, источник и заголовок")
        if self.published_at.utcoffset() is None or self.available_at.utcoffset() is None:
            raise ValueError("published_at и available_at должны включать часовой пояс")
        if self.available_at < self.published_at:
            raise ValueError("событие не может быть доступно до публикации")
        object.__setattr__(self, "published_at", self.published_at.astimezone(MSK))
        object.__setattr__(self, "available_at", self.available_at.astimezone(MSK))
        if self.url:
            link = urlsplit(self.url)
            if link.scheme not in ("https", "http") or not link.hostname or link.username:
                raise ValueError("ссылка на источник должна использовать HTTPS или HTTP без учётных данных")


def _csv_rows(path: Path, required: Iterable[str]) -> Iterator[tuple[int, dict[str, str]]]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        missing = set(required) - set(columns)
        if missing:
            raise ValueError(f"{path}: нет колонок {', '.join(sorted(missing))}")
        if len(set(columns)) != len(columns):
            raise ValueError(f"{path}: повторяются названия колонок")
        for row in reader:
            if None in row or any(row[column] is None for column in required):
                raise ValueError(f"{path}:{reader.line_num}: неверное количество полей")
            yield reader.line_num, row


def load_bars(
    path: Path, *, open_time: time = DEMO_OPEN, close_time: time = ISS_CLOSE
) -> list[Bar]:
    """Load UTF-8 daily CSV; row-level session times override the default clock."""
    bars = []
    seen = set()
    for line, row in _csv_rows(path, BAR_COLUMNS[:7]):
        try:
            bar = Bar(
                symbol=row["symbol"],
                day=date.fromisoformat(row["date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                open_time=_clock(row["open_time"]) if row.get("open_time") else open_time,
                close_time=_clock(row["close_time"]) if row.get("close_time") else close_time,
            )
            key = (bar.symbol, bar.day)
            if key in seen:
                raise ValueError(f"повторяется свеча {bar.symbol} {bar.day}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{line}: {exc}") from exc
        seen.add(key)
        bars.append(bar)
    if not bars:
        raise ValueError(f"{path}: нет свечей")
    return sorted(bars, key=lambda bar: (bar.day, bar.symbol))


def load_events(path: Path) -> list[Event]:
    """Load the text and point-in-time receipt of each archived event."""
    events = []
    seen = set()
    required = ("event_id", "symbol", "published_at", "available_at", "source", "title")
    for line, row in _csv_rows(path, required):
        try:
            item = Event(
                event_id=row["event_id"].strip(),
                symbol=row["symbol"],
                published_at=datetime.fromisoformat(row["published_at"]),
                available_at=datetime.fromisoformat(row["available_at"]),
                source=row["source"].strip(),
                author=(row.get("author") or "").strip(),
                title=row["title"].strip(),
                text=(row.get("text") or "").strip(),
                url=(row.get("url") or "").strip(),
            )
            key = (item.symbol, item.event_id)
            if key in seen:
                raise ValueError(f"повторяется событие {item.symbol} {item.event_id}")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{line}: {exc}") from exc
        seen.add(key)
        events.append(item)
    return sorted(events, key=lambda item: (item.available_at, item.symbol, item.event_id))


def _write_csv(path: Path, columns: Iterable[str], rows: Iterable[Iterable[object]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8", newline="") as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_bars(path: Path, bars: Iterable[Bar]) -> None:
    _write_csv(
        path,
        BAR_COLUMNS,
        (
            (bar.symbol, bar.day, bar.open, bar.high, bar.low, bar.close, bar.volume,
             bar.open_time.isoformat(), bar.close_time.isoformat())
            for bar in sorted(bars, key=lambda bar: (bar.day, bar.symbol))
        ),
    )


def write_events(path: Path, events: Iterable[Event]) -> None:
    _write_csv(
        path,
        EVENT_COLUMNS,
        (
            (item.event_id, item.symbol, item.published_at.isoformat(), item.available_at.isoformat(),
             item.source, item.author, item.title, item.text, item.url)
            for item in sorted(events, key=lambda item: (item.available_at, item.symbol, item.event_id))
        ),
    )


def _iss_page(url: str) -> dict:
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Kontur/1.0"})
        with urlopen(request, timeout=25) as response:
            return json.load(response)
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise ValueError(f"не удалось получить данные MOEX ISS: {exc}") from exc


def download_moex(
    symbols: Iterable[str],
    start: date,
    end: date,
    *,
    open_time: time = DEMO_OPEN,
    close_time: time = ISS_CLOSE,
) -> list[Bar]:
    """Download inclusive TQBR daily history; missing OHLC rows are non-trading days.

    ISS daily rows don't contain exact trade timestamps. Session times are the modeled
    opening and an end-of-day cutoff for information availability, both in Moscow time.
    """
    if start > end:
        raise ValueError("начало периода позже конца")
    if end >= datetime.now(MSK).date():
        raise ValueError("конец периода должен предшествовать текущему дню в МСК")
    tickers = tuple(dict.fromkeys(_symbol(ticker) for ticker in symbols))
    if not tickers:
        raise ValueError("укажите хотя бы один тикер")
    period_days = (end - start).days + 1
    bars = []
    seen = set()
    for ticker in tickers:
        count_before = len(bars)
        offset = 0
        while True:
            query = urlencode({
                "from": start.isoformat(),
                "till": end.isoformat(),
                "start": offset,
                "iss.meta": "off",
                "iss.only": "history,history.cursor",
                "history.columns": ",".join(ISS_COLUMNS),
            })
            reply = _iss_page(f"{ISS_ROOT}/{ticker}.json?{query}")
            try:
                history = reply["history"]
                columns = history["columns"]
                rows = history["data"]
                if not set(ISS_COLUMNS).issubset(columns):
                    raise ValueError("в ответе отсутствуют поля истории")
                for values in rows:
                    row = dict(zip(columns, values, strict=True))
                    if all(row[key] is None for key in ("OPEN", "HIGH", "LOW", "CLOSE")):
                        continue
                    bar = Bar(
                        symbol=row["SECID"],
                        day=date.fromisoformat(row["TRADEDATE"]),
                        open=float(row["OPEN"]),
                        close=float(row["CLOSE"]),
                        high=float(row["HIGH"]),
                        low=float(row["LOW"]),
                        volume=float(row["VOLUME"]),
                        open_time=open_time,
                        close_time=close_time,
                    )
                    if bar.symbol != ticker or not start <= bar.day <= end:
                        raise ValueError("ответ содержит свечу вне запрошенного тикера или периода")
                    key = (bar.symbol, bar.day)
                    if key in seen:
                        raise ValueError(f"ISS вернул повторную свечу {bar.symbol} {bar.day}")
                    seen.add(key)
                    bars.append(bar)
                cursor = reply.get("history.cursor")
                if cursor and cursor["data"]:
                    index = dict(zip(cursor["columns"], cursor["data"][0], strict=True))
                    total = int(index["TOTAL"])
                    if int(index["INDEX"]) != offset:
                        raise ValueError("ISS вернул страницу с неверным смещением")
                else:
                    total = None
                if not rows:
                    if total is not None and offset < total:
                        raise ValueError("пропущена страница истории")
                    break
                offset += len(rows)
                if offset > period_days:
                    raise ValueError("число строк ISS превышает число дней в периоде")
                if total is not None and offset >= total:
                    break
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{ticker}, страница ISS со смещением {offset}: {exc}") from exc
        if len(bars) == count_before:
            raise ValueError(f"MOEX ISS не вернул торгуемые свечи {ticker} за {start} — {end}")
    return sorted(bars, key=lambda bar: (bar.day, bar.symbol))


_DEMO_SYMBOLS = {"SBER": 275.0, "GAZP": 152.0, "LKOH": 6500.0, "YDEX": 3700.0}
_POSITIVE = (
    ("Рост прибыли по итогам квартала", "Компания повысила прогноз прибыли после публикации отчёта."),
    ("Дивиденды утверждены", "Совет директоров рекомендовал увеличить дивиденды."),
    ("Рост объёма производства", "Объём производства вырос по итогам отчётного периода."),
    ("Снятие санкций", "Регулятор отменил ограничения для части операций компании."),
)
_NEGATIVE = (
    ("Убыток по итогам квартала", "Компания сообщила об убытке и ухудшила прогноз."),
    ("Дивиденды сокращены", "Совет директоров сократил дивиденды."),
    ("Сокращение объёма производства", "Компания объявила о снижении объёма производства."),
    ("Введены ограничения", "Регулятор сообщил о новых санкциях для части операций компании."),
)


def synthetic_data(seed: int = 21, sessions: int = 950) -> tuple[list[Bar], list[Event]]:
    """Generate a reproducible test scenario with a deliberately embedded event effect."""
    if sessions < 1:
        raise ValueError("число сессий должно быть положительным")
    rng = random.Random(seed)
    day = date(2023, 1, 9)
    days = []
    while len(days) < sessions:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)

    prices = _DEMO_SYMBOLS.copy()
    bars, events = [], []
    effects: dict[tuple[int, str], float] = {}
    for index, day in enumerate(days):
        regime = (index // 60) % 4
        trend = (0.0006, -0.0004, 0.0000, 0.0003)[regime]
        market = rng.gauss(trend, (0.0040, 0.0070, 0.0055, 0.0045)[regime])
        for symbol, prior in prices.items():
            signal = effects.pop((index, symbol), 0)
            opening = prior * math.exp(rng.gauss(0, 0.003))
            closing = opening * math.exp(market + signal + rng.gauss(0, 0.007))
            high = max(opening, closing) * math.exp(abs(rng.gauss(0.002, 0.002)))
            low = min(opening, closing) * math.exp(-abs(rng.gauss(0.002, 0.002)))
            volume = round(rng.lognormvariate(14.0, 0.55) * (1 + 20 * abs(signal)))
            prices[symbol] = closing
            bars.append(Bar(symbol, day, round(opening, 4), round(high, 4),
                            round(low, 4), round(closing, 4), volume))

            if rng.random() >= 0.25:
                continue
            direction = rng.choice((-1, 1))
            title, description = rng.choice(_POSITIVE if direction > 0 else _NEGATIVE)
            published_day = day
            if day.weekday() == 4 and rng.random() < 0.15:
                published_day += timedelta(days=1)
            published_at = datetime.combine(published_day, time(17), MSK)
            receipt_delay = rng.choice((15, 20, 40, 130, 180))
            available_at = published_at + timedelta(minutes=receipt_delay)
            event_id = f"demo-{symbol.lower()}-{index + 1:04d}"
            reliable = rng.random() < 0.75
            source = "Синтетическая лента" if reliable else "Синтетический комментарий"
            author = "Учебный релиз" if reliable else "Учебный наблюдатель"
            events.append(Event(event_id, symbol, published_at, available_at,
                                source, author, title, description))

            delayed = published_day != day or available_at.time() > DEMO_CLOSE
            effect_index = index + (2 if delayed else 1)
            if reliable and effect_index < sessions:
                key = (effect_index, symbol)
                shocks = direction * max(0.0, rng.gauss(0.012, 0.004))
                effects[key] = effects.get(key, 0) + shocks
    return (
        sorted(bars, key=lambda bar: (bar.day, bar.symbol)),
        sorted(events, key=lambda item: (item.available_at, item.symbol, item.event_id)),
    )
