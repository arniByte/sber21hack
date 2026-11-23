"""Optional server-side report explainer for local sessions."""

import json
import os
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
SLOTS = BoundedSemaphore(1)
DEFAULT_MODEL = "deepseek/deepseek-v3.2"
MAX_PRICE = {"prompt": 0.40, "completion": 1.60}
METRICS = ("return_pct", "net_pnl", "trade_count", "trade_days", "max_drawdown_pct", "profit_factor")


def enabled():
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def report_context(report, agent, period):
    if isinstance(agent, str) and isinstance(period, (str, type(None))):
        study = report.get("long_study", {}).get("strategies", {}).get(agent)
        if study:
            picked = study.get("fresh") if period == "recent" else next(
                (w for w in study["horizons"] if w["available"] and str(w["months"]) == period), None)
            if picked:
                return {"period": [picked["start"], picked["end"]], "dataset": report["meta"]["dataset"],
                        "strategy": agent, "metrics": {name: picked.get(name) for name in (
                            "return_pct", "net_pnl", "trade_count", "drawdown_pct", "profit_factor",
                            "stress_pct", "matched_risk_pct", "concentration_pct"
                        )}}
        daily = report["strategies"].get(agent)
        if daily:
            meta = report["meta"]
            if period in (None, "base"):
                start, end, stats = meta["oos_start"], meta["oos_end"], daily["metrics"]
            else:
                window = next((w for w in report.get("horizons", [])
                               if w["available"] and str(w["months"]) == period), None)
                if not window or report.get("preset", {}).get("agent") != agent:
                    raise ValueError("Период недоступен для этой стратегии.")
                start, end, stats = window["start"], window["end"], window["metrics"]
            return {"period": [start, end], "dataset": meta["dataset"], "strategy": agent,
                    "metrics": {name: stats.get(name) for name in METRICS}}
    raise ValueError("Выберите доступную стратегию и период.")


def answer(report, agent, question, period=None):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError("Задайте вопрос до 500 символов.")
    context = report_context(report, agent, period)
    return complete([
        {"role": "system", "content":
         "Ты Мишка, аналитик исторических симуляций Контур. Отвечай коротко по-русски без разметки. "
         "Числа бери из переданной сводки. Различай симуляцию и реальный счёт. "
         "Если данных недостаточно, укажи каких. Не обещай прибыль."},
        {"role": "user", "content": json.dumps({"summary": context, "question": question}, ensure_ascii=False)},
    ])


def complete(messages, *, max_tokens=420):
    if not enabled():
        raise ValueError("Задайте OPENROUTER_API_KEY.")
    payload = {"model": os.environ.get("OPENROUTER_MODEL") or DEFAULT_MODEL, "max_tokens": max_tokens,
               "messages": messages, "provider": {"max_price": MAX_PRICE}}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > 64_000:
        raise ValueError("Сводка для OpenRouter слишком длинная.")
    req = Request(ENDPOINT, method="POST", data=encoded,
                  headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"],
                           "Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=25) as response:
            message = json.loads(response.read(128_000))["choices"][0]["message"]["content"]
        if not isinstance(message, str) or not message.strip():
            raise ValueError("empty response")
        return message.strip()
    except (HTTPError, URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("OpenRouter не вернул ответ. Доступен локальный разбор.") from error
