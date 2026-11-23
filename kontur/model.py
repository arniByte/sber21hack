"""Point-in-time market and event features and expanding-window forecasts."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
import re
from typing import TYPE_CHECKING, Iterable

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

if TYPE_CHECKING:
    from .data import Bar, Event


PRICE_FEATURES = (
    "return_1", "return_3", "return_5", "return_10", "volatility_5",
    "volatility_20", "daily_range", "open_gap", "intraday_move", "volume_z",
)
EVENT_CATEGORIES = ("dividend", "earnings", "rates", "sanctions", "operations", "other")
EVENT_FEATURES = (
    "fresh_count", "fresh_positive", "fresh_negative", "decayed_count",
    "decayed_sentiment", "post_event_return", "fresh_sentiment_x_volume",
    "fresh_source_count", "fresh_author_count", "source_consensus", "author_consensus",
    *(f"fresh_{category}_sentiment" for category in EVENT_CATEGORIES),
)
FEATURE_NAMES = PRICE_FEATURES + EVENT_FEATURES
LOOKBACK = 20
EVENT_SESSIONS = 5
MODEL_NAME = "Ridge"
MODEL_ALPHA = 18.0
EVENT_ENCODER = "Тематический словарь с определением тональности и локального отрицания"
MARKET_SYMBOLS = {"MARKET", "IMOEX", "*"}

_CATEGORIES = (
    ("dividend", r"\bдивиденд|\bdividend"),
    ("earnings", r"\bприбыл|\bубыт|\bвыруч|\bотч[её]тност|\bearnings|\brevenue"),
    ("rates", r"\bключев\w*\s+ставк|\bпроцентн\w*\s+ставк|\binterest rate"),
    ("sanctions", r"\bсанкц|\bограничен|\bsanction|\brestriction"),
    ("operations", r"\bпроизводств|\bпостав|\bконтракт|\bproduction|\boutput"),
)
_POSITIVE = re.compile(
    r"\b(рост\w*|вырос\w*|увелич\w*|повышен\w*|одобрен\w*|утвержден\w*|"
    r"улучш\w*|превыс\w*|surpass\w*|growth|raise\w*|approv\w*|beat\w*)\b"
)
_NEGATIVE = re.compile(
    r"\b(убыт\w*|паден\w*|сокращ\w*|сниз\w*|снижен\w*|отмен\w*|"
    r"ухудш\w*|слаб\w*|обвал\w*|cut\w*|loss\w*|declin\w*|miss\w*)\b"
)
_RATES_POSITIVE = re.compile(r"\b(снижен\w*|сниз\w*|сокращ\w*|cut\w*|lower\w*)\b")
_RATES_NEGATIVE = re.compile(r"\b(повыш\w*|увелич\w*|рост\w*|hik\w*|rais\w*)\b")
_SANCTIONS_POSITIVE = re.compile(r"\b(сня[лт]\w*|отмен\w*|смягч\w*|lift\w*|relax\w*)\b")
_SANCTIONS_NEGATIVE = re.compile(r"\b(введ\w*|ужесточ\w*|расшир\w*|impos\w*|tighten\w*)\b")


@dataclass(frozen=True, slots=True)
class EventEvidence:
    event_id: str
    source: str
    author: str
    title: str
    url: str
    published_at: datetime
    available_at: datetime
    category: str
    sentiment: float


@dataclass(frozen=True, slots=True)
class FeatureRow:
    ticker: str
    signal_day: date
    trade_day: date | None
    signal_at: datetime
    values: tuple[float, ...]
    target: float | None
    momentum_return: float
    evidence: tuple[EventEvidence, ...]


@dataclass(frozen=True, slots=True)
class Prediction:
    ticker: str
    signal_day: date
    trade_day: date
    signal_at: datetime
    market_return: float
    event_return: float
    momentum_return: float
    actual_return: float
    fold: int
    evidence: tuple[EventEvidence, ...]


@dataclass(frozen=True, slots=True)
class LatestForecast:
    ticker: str
    signal_day: date
    signal_at: datetime
    market_return: float
    event_return: float
    momentum_return: float
    evidence: tuple[EventEvidence, ...]


@dataclass(frozen=True, slots=True)
class Fold:
    fold: int
    train_start: date
    train_end: date
    train_label_end: date
    test_start: date
    test_end: date
    train_rows: int
    test_rows: int


@dataclass(frozen=True, slots=True)
class ForecastResult:
    predictions: tuple[Prediction, ...]
    folds: tuple[Fold, ...]
    diagnostics: dict[str, dict[str, float | int]]


def _polarity(text: str, positive: re.Pattern[str], negative: re.Pattern[str]) -> float:
    """Recognize signed phrases, with a small local negation window."""
    score = 0
    for sign, pattern in ((1, positive), (-1, negative)):
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 25):match.start()]
            negated = re.search(r"\b(не|not|no)\s+(?:[\w-]+\s+)?$", prefix)
            score += -sign if negated else sign
    return float(np.sign(score))


def summarize_event(event: Event) -> EventEvidence:
    """Extract a compact category and signed lexical score with source provenance."""
    if event.available_at.utcoffset() is None or event.published_at.utcoffset() is None:
        raise ValueError("Event timestamps must include a timezone")
    title = event.title.casefold().replace("ё", "е")
    body = event.text.casefold().replace("ё", "е")[:2000]
    category = "other"
    for candidate_text in (title, body):
        category = next(
            (name for name, pattern in _CATEGORIES if re.search(pattern, candidate_text)),
            "other",
        )
        if category != "other":
            break
    positive, negative = {
        "rates": (_RATES_POSITIVE, _RATES_NEGATIVE),
        "sanctions": (_SANCTIONS_POSITIVE, _SANCTIONS_NEGATIVE),
    }.get(category, (_POSITIVE, _NEGATIVE))
    sentiment = _polarity(title, positive, negative) or _polarity(body, positive, negative)
    return EventEvidence(
        event.event_id, event.source, event.author, event.title, event.url,
        event.published_at, max(event.published_at, event.available_at), category, sentiment,
    )


def _price_features(bars: list[Bar], index: int) -> tuple[float, ...]:
    bar = bars[index]
    closes = np.array([item.close for item in bars[index - LOOKBACK:index + 1]], dtype=float)
    returns = np.clip(closes[1:] / closes[:-1] - 1, -.2, .2)
    changes = [float(np.clip(bar.close / closes[-1 - lag] - 1, -.3, .3)) for lag in (1, 3, 5, 10)]
    prior_volumes = np.log1p([item.volume for item in bars[index - LOOKBACK:index]])
    volume_sd = float(np.std(prior_volumes))
    volume_z = (
        float(np.clip((np.log1p(bar.volume) - np.mean(prior_volumes)) / volume_sd, -5, 5))
        if volume_sd > 1e-8 else 0.0
    )
    return (
        *changes, float(np.std(returns[-5:])), float(np.std(returns)),
        min((bar.high - bar.low) / bar.open, .2), float(np.clip(bar.open / closes[-2] - 1, -.2, .2)),
        bar.close / bar.open - 1, volume_z,
    )


def _event_features(
    recent: list[tuple[EventEvidence, int, float]], close: float, volume_z: float,
) -> tuple[float, ...]:
    fresh = [event for event, age, _ in recent if age == 0]
    positive = sum(event.sentiment > 0 for event in fresh)
    negative = sum(event.sentiment < 0 for event in fresh)
    weights = [0.5 ** age for _, age, _ in recent]
    decayed_count = sum(weights)
    decayed_sentiment = sum(event.sentiment * weight for (event, _, _), weight in zip(recent, weights))
    reactions = [
        float(np.clip(close / reference - 1, -.15, .15)) * weight
        for (_, _, reference), weight in zip(recent, weights)
    ]
    return (
        float(len(fresh)), float(positive), float(negative), decayed_count,
        decayed_sentiment, sum(reactions) / decayed_count if decayed_count else 0.0,
        (positive - negative) * volume_z,
        float(len({_identity(event.source) for event in fresh} - {""})),
        float(len({_identity(event.author) for event in fresh} - {""})),
        _consensus(recent, "source"), _consensus(recent, "author"),
        *(sum(event.sentiment for event in fresh if event.category == category) for category in EVENT_CATEGORIES),
    )


def _identity(value: str) -> str:
    return " ".join(value.casefold().split())


def _consensus(recent: list[tuple[EventEvidence, int, float]], field: str) -> float:
    """Assign one vote per distinct source or author to temper repeated headlines."""
    groups: dict[str, float] = defaultdict(float)
    for event, age, _ in recent:
        identity = _identity(getattr(event, field))
        if identity:
            groups[identity] += event.sentiment * 0.5 ** age
    votes = [int(value > 0) - int(value < 0) for value in groups.values() if value]
    return float(sum(votes) / len(votes)) if len(votes) >= 2 else 0.0


def _market_days(bars: Iterable[Bar]) -> tuple[list[date], dict[str, list[Bar]]]:
    by_symbol: dict[str, list[Bar]] = defaultdict(list)
    observed: set[tuple[str, date]] = set()
    for bar in bars:
        key = (bar.symbol, bar.day)
        if key in observed:
            raise ValueError(f"Duplicate daily bar: {bar.symbol} {bar.day}")
        observed.add(key)
        by_symbol[bar.symbol].append(bar)
    return sorted({day for _, day in observed}), by_symbol


def build_features(bars: Iterable[Bar], events: Iterable[Event] = ()) -> list[FeatureRow]:
    """Join events by availability and predict the next observed market session only."""
    all_days, by_symbol = _market_days(bars)
    day_index = {day: index for index, day in enumerate(all_days)}
    event_groups: dict[str, list[EventEvidence]] = defaultdict(list)
    event_ids: set[tuple[str, str]] = set()
    for event in events:
        symbol = "*" if event.symbol in MARKET_SYMBOLS else event.symbol
        key = (symbol, event.event_id)
        if key in event_ids:
            raise ValueError(f"Duplicate event: {event.symbol} {event.event_id}")
        event_ids.add(key)
        event_groups[symbol].append(summarize_event(event))

    output = []
    for ticker, history in by_symbol.items():
        history.sort(key=lambda bar: bar.day)
        by_day = {bar.day: bar for bar in history}
        closes = [bar.close_at for bar in history]
        relevant = sorted(
            [*event_groups[ticker], *event_groups["*"]],
            key=lambda event: (event.available_at, event.event_id),
        )
        event_times = [event.available_at for event in relevant]
        for index, bar in enumerate(history[LOOKBACK:], start=LOOKBACK):
            day_position = day_index[bar.day]
            trade_day = all_days[day_position + 1] if day_position + 1 < len(all_days) else None
            trade_bar = by_day.get(trade_day)
            target = (
                trade_bar.close / trade_bar.open - 1
                if trade_bar is not None and trade_bar.open_at > bar.close_at else None
            )

            # Find the first bar where the event could have been acted on. Older
            # bars establish the reaction without consulting the target session.
            history_start = index - EVENT_SESSIONS
            first = bisect_right(event_times, closes[history_start])
            end = bisect_right(event_times, bar.close_at)
            recent = []
            for event in relevant[first:end]:
                event_index = bisect_left(closes, event.available_at, lo=history_start + 1, hi=index + 1)
                event_age = day_position - day_index[history[event_index].day]
                if event_age < EVENT_SESSIONS:
                    recent.append((event, event_age, history[event_index].close))

            price_values = _price_features(history, index)
            event_values = _event_features(recent, bar.close, price_values[-1])
            # Compare against a fixed, causal extrapolation of the previous five
            # open-to-close returns, with moderate shrinkage toward zero.
            momentum = sum(item.close / item.open - 1 for item in history[index - 4:index + 1]) / 10
            evidence = tuple(event for event, _, _ in reversed(recent[-8:]))
            output.append(FeatureRow(
                ticker, bar.day, trade_day, bar.close_at, price_values + event_values,
                target, momentum, evidence,
            ))
    return sorted(output, key=lambda row: (row.signal_day, row.ticker))


def execution_targets(rows, bars, risk):
    """Only replace resolved labels. Features and all decision timestamps stay intact."""
    from .backtest import resolve_exit

    lookup = {(bar.symbol, bar.day): bar for bar in bars}
    output = []
    for row in rows:
        if row.target is None:
            output.append(row)
            continue
        bar = lookup[row.ticker, row.trade_day]
        price, _, _ = resolve_exit(bar, bar.open * (1 - risk.stop_pct / 100),
                                     bar.open * (1 + risk.take_profit_pct / 100))
        output.append(replace(row, target=price / bar.open - 1))
    return output


def _diagnostics(predictions: list[Prediction], field: str) -> dict[str, float | int]:
    forecast = np.array([getattr(item, field) for item in predictions])
    actual = np.array([item.actual_return for item in predictions])
    error = forecast - actual
    return {
        "samples": len(predictions),
        "mae_bps": float(np.mean(np.abs(error)) * 10_000),
        "rmse_bps": float(np.sqrt(np.mean(error ** 2)) * 10_000),
        "direction_accuracy": float(np.mean(np.sign(forecast) == np.sign(actual))),
    }


def _fit_predict(
    train: list[FeatureRow], score: list[FeatureRow],
) -> tuple[np.ndarray, np.ndarray]:
    market_width = len(PRICE_FEATURES)
    train_x = np.array([row.values for row in train], dtype=float)
    train_y = np.array([row.target for row in train], dtype=float)
    score_x = np.array([row.values for row in score], dtype=float)
    # Identical estimator and fixed regularization isolate feature-set changes.
    market = make_pipeline(StandardScaler(), Ridge(alpha=MODEL_ALPHA))
    event = make_pipeline(StandardScaler(), Ridge(alpha=MODEL_ALPHA))
    with threadpool_limits(limits=1):
        market.fit(train_x[:, :market_width], train_y)
        event.fit(train_x, train_y)
        return market.predict(score_x[:, :market_width]), event.predict(score_x)


def walk_forward(
    rows: Iterable[FeatureRow], min_train_days: int = 120, test_days: int = 30,
    holdout_days: int | None = None,
) -> ForecastResult:
    """Fit on resolved labels only, then predict untouched complete dates in blocks."""
    if min_train_days < 2 or test_days < 1 or (holdout_days is not None and holdout_days < 1):
        raise ValueError("Invalid chronological training window")

    labels = sorted(
        (row for row in rows if row.target is not None and row.trade_day is not None),
        key=lambda row: (row.signal_day, row.ticker),
    )
    days = sorted({row.signal_day for row in labels})
    first_test = min_train_days + 1
    if holdout_days is not None:
        first_test = max(first_test, len(days) - holdout_days)
    if first_test >= len(days):
        raise ValueError(f"Need more labeled market sessions after warm-up. Available: {len(days)}")

    predictions: list[Prediction] = []
    folds: list[Fold] = []
    for start in range(first_test, len(days), test_days):
        test_start = days[start]
        test_end = days[min(start + test_days, len(days)) - 1]
        train = [row for row in labels if row.signal_day < test_start and row.trade_day < test_start]
        test = [row for row in labels if test_start <= row.signal_day <= test_end]
        if len({row.signal_day for row in train}) < min_train_days:
            continue
        fold = len(folds) + 1
        market_scores, event_scores = _fit_predict(train, test)
        for row, market_score, event_score in zip(test, market_scores, event_scores):
            predictions.append(Prediction(
                row.ticker, row.signal_day, row.trade_day, row.signal_at,
                float(market_score), float(event_score), row.momentum_return, row.target,
                fold, row.evidence,
            ))
        folds.append(Fold(
            fold, train[0].signal_day, train[-1].signal_day, max(row.trade_day for row in train),
            test_start, test_end, len(train), len(test),
        ))
    if not predictions:
        raise ValueError("No out-of-sample dates with enough resolved training labels")
    return ForecastResult(tuple(predictions), tuple(folds), {
        "market": _diagnostics(predictions, "market_return"),
        "event": _diagnostics(predictions, "event_return"),
        "momentum": _diagnostics(predictions, "momentum_return"),
    })


def latest_forecast(rows: Iterable[FeatureRow], min_train_days: int = 120) -> tuple[LatestForecast, ...]:
    """Score the newest observed close separately from historical predictions."""
    ordered = sorted(rows, key=lambda row: (row.signal_day, row.ticker))
    if not ordered:
        return ()
    latest_day = ordered[-1].signal_day
    train = [
        row for row in ordered
        if row.target is not None and row.trade_day is not None and row.trade_day < latest_day
    ]
    if len({row.signal_day for row in train}) < min_train_days:
        return ()
    latest = [row for row in ordered if row.signal_day == latest_day]
    market_scores, event_scores = _fit_predict(train, latest)
    return tuple(
        LatestForecast(
            row.ticker, row.signal_day, row.signal_at, float(market_score),
            float(event_score), row.momentum_return, row.evidence,
        )
        for row, market_score, event_score in zip(latest, market_scores, event_scores)
    )
