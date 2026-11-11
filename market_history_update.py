"""Market history ingestion: official CSI index; optional third-party ETF history.

No AI, no guessing missing PE/PB, and no accidental zero-value substitution.
Market data is always separated by instrument and provider.
"""
import datetime as dt
import json
import math
import os
import re
import time
from pathlib import Path

import requests

CONFIG = Path("data/market_config.json")
OUT = Path("data/market_history.json")
CSI_API = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
ETF_API = "https://api.databull.cn/cn/etf/history"
MAX_ROWS = 1800   # about seven years of trading days
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; GuoceDashboardMarketResearch/1.0)",
    "Referer": "https://www.csindex.com.cn/",
    "Accept": "application/json",
})
VALID_DATE = re.compile(r"^20\d{2}-\d{2}-\d{2}$")
POSITIVES = ("close", "pe_ttm", "pb")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def datefmt(raw):
    raw = str(raw or "")[:10].replace("/", "-")
    if re.fullmatch(r"\d{8}", raw):
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    if not VALID_DATE.fullmatch(raw):
        return None
    try:
        return dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def numeric(raw, positive=False):
    try:
        n = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n) or (positive and n <= 0) or n < 0 and positive:
        return None
    return n


def pick_number(row, keys, positive=False):
    for key in keys:
        if key in row:
            n = numeric(row[key], positive=positive)
            if n is not None:
                return n
    return None


def parse_csi(raw, code):
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), list):
        raise ValueError("中证历史接口未返回 data 列表")
    points = []
    for rec in raw["data"]:
        if not isinstance(rec, dict):
            continue
        supplied = str(rec.get("indexCode", "") or "").strip()
        if supplied and supplied != code:
            continue
        date = datefmt(rec.get("tradeDate"))
        close = pick_number(rec, ["close"], positive=True)
        if not date or close is None:
            continue
        point = {
            "date": date,
            "close": close,
            "turnover": pick_number(rec, ["amount", "turnover", "dealAmount"], positive=False),
            "turnover_unit": "source_units_unconfirmed",
            "pe_ttm": pick_number(rec, ["peg"], positive=True),
            "pb": None,
            "source_url": CSI_API,
        }
        points.append(point)
    if not points:
        raise ValueError("中证指数无有效历史数据行")
    return points


def parse_etf(raw):
    if not isinstance(raw, list):
        raise ValueError("ETF接口未返回日线数组")
    rows = []
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        day = datefmt(rec.get("date"))
        close = numeric(rec.get("close"), positive=True)
        if not day or close is None:
            continue
        rows.append({
            "date": day, "close": close,
            "volume": numeric(rec.get("volume")),
            # ETF endpoint does not document a turnover-money field.
            "turnover": None, "turnover_unit": None,
            "pe_ttm": None, "pb": None,
            "source_url": ETF_API,
        })
    if not rows:
        raise ValueError("ETF接口未返回有效日线")
    return rows


def merge(old, newer):
    """Do not drop historical data or rewrite previously verified fields to null."""
    rows = {p["date"]: dict(p) for p in old if isinstance(p, dict) and p.get("date")}
    changes = 0
    for p in newer:
        old_row = rows.get(p["date"], {})
        patch = {k: v for k, v in p.items() if v is not None}
        candidate = {**old_row, **patch}
        if candidate != old_row:
            rows[p["date"]] = candidate
            changes += 1
    return sorted(rows.values(), key=lambda p: p["date"])[-MAX_ROWS:], changes


def fetch_csi(code, start, end):
    r = SESSION.get(CSI_API, params={"indexCode": code, "startDate": start, "endDate": end},
                    timeout=22, allow_redirects=False)
    r.raise_for_status()
    return parse_csi(r.json(), code)


def fetch_etf(code, start, end, api_key):
    r = SESSION.get(ETF_API, params={"symbol": code, "start_date": start,
                                   "end_date": end, "period": "d", "adj": "none"},
                    headers={"Authorization": "Bearer " + api_key},
                    timeout=22, allow_redirects=False)
    r.raise_for_status()
    return parse_etf(r.json())


def percentile(points):
    """A PE percentile requires 3y + 750 valid same-source daily samples."""
    valid = [(p.get("date"), numeric(p.get("pe_ttm"), positive=True))
             for p in points if p.get("pe_ttm") is not None]
    valid = [(d, pe) for d, pe in valid if d and pe is not None]
    if len(valid) < 750:
        return None
    last = dt.date.fromisoformat(valid[-1][0])
    three_years = (last - dt.timedelta(days=1095))
    if dt.date.fromisoformat(valid[0][0]) > three_years:
        return None
    pe = valid[-1][1]
    return round(100 * sum(v <= pe for _, v in valid) / len(valid), 1)


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    current = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    recorded = {r["id"]: r for r in current.get("instruments", []) if r.get("id")}
    statuses = []
    key = os.getenv("DATABULL_API_KEY", "").strip()
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    end = today.strftime("%Y%m%d")
    for kind, entries in (("index", cfg.get("indices", [])), ("etf", cfg.get("etfs", []))):
        for info in entries:
            id_ = info["id"]
            record = recorded.get(id_, {})
            record.update({**info, "kind": kind})
            record.setdefault("points", [])
            old = record["points"]
            # Backfill up to 6y on first run; later runs look back 10 days
            # to capture provider corrections without data gaps.
            start = ((today - dt.timedelta(days=2190)) if not old else
                     dt.date.fromisoformat(old[-1]["date"]) - dt.timedelta(days=10)).strftime("%Y%m%d")
            status = {"instrument_id": id_, "checked_at": now()}
            if kind == "etf" and not key:
                status.update({"state": "requires_data_key", "message": "未配置DATABULL_API_KEY；ETF日线保留已有数据"})
                statuses.append(status)
                recorded[id_] = record
                continue
            try:
                fresh = fetch_csi(info["code"], start, end) if kind == "index" else \
                        fetch_etf(info["code"], start, end, key)
                merged, changed = merge(old, fresh)
                record["points"] = merged
                record["last_data_date"] = merged[-1]["date"]
                record["pe_percentile"] = percentile(merged) if kind == "index" else None
                status.update({"state": "ok", "new_or_updated_rows": changed,
                               "rows_total": len(merged), "last_data_date": merged[-1]["date"]})
                print("行情采集成功", id_, "rows", len(merged), "changes", changed)
            except (requests.RequestException, ValueError, KeyError, TypeError) as e:
                status.update({"state": "failed", "message": type(e).__name__})
                print("行情采集异常", id_, type(e).__name__)
            recorded[id_] = record
            statuses.append(status)
            time.sleep(0.25)
    out = {
        "schema_version": 1, "updated_at": now(),
        "methodology": cfg["methodology"],
        "sources": {"index": CSI_API, "etf": ETF_API},
        "instruments": list(recorded.values()), "status": statuses,
    }
    temp = OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(OUT)
    print("市场数据更新：指数", len(cfg.get("indices", [])),
          "ETF", len(cfg.get("etfs", [])),
          "成功", sum(x["state"] == "ok" for x in statuses),
          "需密钥", sum(x["state"] == "requires_data_key" for x in statuses))


if __name__ == "__main__":
    main()
