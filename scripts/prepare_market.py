"""Prepare the pinned market snapshot used in the published historical experiment."""
import argparse
import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from statistics import median
from urllib.request import urlopen

OLD = "https://raw.githubusercontent.com/WISEPLAT/shares_get_history_class/bff140ee0d626aec46fa85e2dfa9310d74c7c1db/csv_export_rus/D1/"
NEW = "https://raw.githubusercontent.com/vitalio1895/moex-candles/fd2589ae2229c0ebab93717cae32382e80d4100d/combined/1d_daily_all_tickers.csv"
DIGESTS = {
    "GAZP": "213ed7ba36e6a58ef197b95da4228547f6d43883508992ced2a3f59c8734131e",
    "LKOH": "65a851c406feefefd59b98db383564a78d1dcede4037c08f1701c732ed983487",
    "SBER": "9c5b342ca7a951aab91cc3360cee256a9aa534b88767f2e1085f41c8b0bfa38f",
    "TATN": "20ba21db8b7d937c47db2e8edbcbcddd92895a2313d2408bbd64102b0669dd65",
    "recent": "0443d3b791f2877e481cabbaf4102bb547761fa59c3eb921f699aae8936b45ea",
}
SYMBOLS = ("GAZP", "LKOH", "SBER", "TATN")
CUTOFF = "2024-01-01"
FIELDS = ("symbol", "date", "open", "high", "low", "close", "volume")


def fetch(root, name, url):
    path = root / (name + ".csv")
    if path.exists():
        content = path.read_bytes()
    else:
        with urlopen(url, timeout=60) as response:
            content = response.read()
    if hashlib.sha256(content).hexdigest() != DIGESTS[name]:
        raise ValueError(f"Контрольная сумма архива {name} отличается от зафиксированной.")
    if not path.exists():
        path.write_bytes(content)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def save(path, records):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def prepare(out, start="2020-01-01"):
    cache = out / "archive"
    cache.mkdir(parents=True, exist_ok=True)
    latest = [{**row, "date": row["datetime_msk"][:10], "symbol": row["ticker"]}
              for row in fetch(cache, "recent", NEW) if row["session"] == "REGULAR"]
    days = sorted({row["date"] for row in latest})
    save(out / "market-snapshot.csv", (row for row in latest if row["date"] in days[-2:]))
    records, checks = [], {}
    for ticker in SYMBOLS:
        old = {row["datetime"]: row for row in fetch(cache, ticker, OLD + ticker + "_D1.csv")}
        new = {row["date"]: row for row in latest if row["symbol"] == ticker}
        overlap = sorted(day for day in old.keys() & new.keys() if day < CUTOFF)[-30:]
        if len(overlap) != 30:
            raise ValueError(f"Короткое пересечение рядов {ticker}")
        delta = [abs(float(old[d]["close"]) / float(new[d]["close"]) - 1) for d in overlap]
        if max(delta) > 0.01:
            raise ValueError(f"Расхождение цен {ticker} на стыке превышает 1%")
        # Convert later volumes to the early archive unit using only pre-cutover overlap.
        factor = 10 if ticker == "SBER" else 1
        ratio = median(float(new[d]["volume"]) / float(old[d]["volume"]) for d in overlap)
        if not 0.95 < ratio / factor < 1.05:
            raise ValueError(f"Единицы объёма {ticker} расходятся")
        checks[ticker] = {"overlap_sessions": len(overlap), "max_close_gap_pct": max(delta)*100,
                          "volume_scale": factor}
        records.extend({**row, "symbol": ticker, "date": day}
                       for day, row in old.items() if start <= day < CUTOFF)
        records.extend({**row, "volume": float(row["volume"]) / factor}
                       for day, row in new.items() if day >= CUTOFF)
    save(out / "market-bars.csv", sorted(records, key=lambda row: (row["date"], row["symbol"])))
    (out / "sources.json").write_text(json.dumps({"start": start, "cutover": CUTOFF, "checks": checks,
        "source_hashes": DIGESTS, "as_of": days[-1]}, indent=2), encoding="utf-8")
    print(f"{len(records)} свечей; архив по {days[-1]} включительно.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data"))
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    args = parser.parse_args()
    prepare(args.output, args.start.isoformat())
