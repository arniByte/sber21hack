"""Optional server-side report explainer for local sessions."""

import json
import os
from threading import BoundedSemaphore
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
SLOTS = BoundedSemaphore(1)
METRICS = ("return_pct", "net_pnl", "trade_count", "trade_days", "max_drawdown_pct", "profit_factor")


def enabled():
    return bool(os.environ.get("OPENROUTER_API_KEY") and os.environ.get("OPENROUTER_MODEL"))


def answer(report, agent, question):
    if (not isinstance(agent, str) or agent not in report["strategies"] or
            not isinstance(question, str) or not 1 <= len(question.strip()) <= 500):
        raise ValueError("Выберите стратегию и задайте вопрос до 500 символов.")
    stats = report["strategies"][agent]["metrics"]
    context = {"period": [report["meta"]["oos_start"], report["meta"]["oos_end"]],
               "dataset": report["meta"]["dataset"], "strategy": agent,
               "metrics": {name: stats.get(name) for name in METRICS}}
    if report.get("preset", {}).get("agent") == agent:
        context["windows"] = [{"months": w["months"], "return_pct": w["metrics"]["return_pct"],
                               "halt": w["metrics"]["risk_halt_date"]}
                              for w in report.get("horizons", []) if w["available"]]
    payload = {"model": os.environ["OPENROUTER_MODEL"], "max_tokens": 420,
               "messages": [{"role": "system", "content":
                   "Ты Мишка, аналитик исторических симуляций Контур. Отвечай кратко по-русски. "
                   "Числа бери из переданной сводки. Различай симуляцию и реальный счёт. "
                   "Если данных недостаточно, укажи каких. Не обещай прибыль."},
                            {"role": "user", "content": json.dumps({"summary": context, "question": question},
                                                                   ensure_ascii=False)}]}
    req = Request(ENDPOINT, method="POST", data=json.dumps(payload).encode(),
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
