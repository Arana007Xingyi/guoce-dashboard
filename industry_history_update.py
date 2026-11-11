"""Append verifiable official NBS MONTHLY industrial production points (no AI).

Does not re-estimate missing values or silently replace previously verified points.
A title-level source search is not enough: a candidate month is accepted only
when its published official page has table rows with the exact metric.
"""
import datetime
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup

PATH = Path("data/industry_history.json")
INDEX = "https://www.stats.gov.cn/sj/zxfb/"
TIMEOUT = 18
TITLE = re.compile(r"(20\d{2})\s*年\s*(1[0-2]|[1-9])\s*月(?:份)?规模以上工业增加值")
CELL_TARGETS = {
    "hi_tech": "高技术制造业",
    "electronics": "计算机、通信和其他电子设备制造业",
    "ic_output": "集成电路",
    "robot_output": "工业机器人",
    "nev_output": "新能源汽车",
    "pharma": "医药制造业",
    "special_machinery": "专用设备制造业",
    "electrical": "电气机械和器材制造业",
    "aerospace": "铁路、船舶、航空航天和其他运输设备制造业",
    "food": "食品制造业",
    "metal": "有色金属冶炼和压延加工业",
    "agri": "农副食品加工业",
    "solar": "太阳能电池（光伏电池）",
}
# Production table may include Chinese and Latin punctuation.
NORMALIZE = str.maketrans({
    " ": "", "\u3000": "", "，": "", ",": "", "、": "",
    "（": "(", "）": ")", "—": "-", "－": "-"
})
COUNT_METRICS = {"ic_output", "robot_output", "nev_output", "solar"}
CURRENT_MONTH = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m")
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; GuoceDashboardOfficialStatsMonitor/1.0)",
    "Accept-Language": "zh-CN,zh;q=0.9",
})


def official(url):
    u = urlparse(url)
    h = (u.hostname or "").lower()
    return (u.scheme == "https" and u.port in (None, 443)
            and not u.username and not u.password
            and (h == "stats.gov.cn" or h.endswith(".stats.gov.cn")))


def fetch(url):
    if not official(url):
        raise ValueError("Source must be official NBS HTTPS")
    resp = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    if not official(resp.url):
        raise ValueError("NBS source redirected outside official domain")
    resp.encoding = resp.apparent_encoding or "utf-8"
    return BeautifulSoup(resp.text, "html.parser")


def norm(text):
    return " ".join(str(text).split()).translate(NORMALIZE).replace("\xa0", "")


def month_from_title(title):
    match = TITLE.search(title)
    if not match:
        return None
    month = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
    return month if "2026-10" <= month <= CURRENT_MONTH else None


def discover():
    """Look for pages explicitly titled as NBS monthly industrial production."""
    pages = {}
    # The official index is paginated and sometimes cached; bounded discovery
    # avoids silently missing a month when it slips beyond the first page.
    for listing in [INDEX, INDEX + "index_1.html", INDEX + "index_2.html"]:
        try:
            soup = fetch(listing)
        except (requests.RequestException, ValueError):
            continue
        for a in soup.select("a[href]"):
            period = month_from_title(a.get_text(" ", strip=True))
            if not period:
                continue
            target = urldefrag(urljoin(listing, a.get("href", "")))[0]
            if official(target) and target.endswith(".html"):
                pages.setdefault(period, target)
    return pages


def industry_table(soup):
    """Extract only exact first-column labels, with monthly YoY as column 3.

    For industrial value added, first data column is an ellipsis; for product
    output, first data column is absolute production. The next column is monthly
    YoY in both tables. Ignore cumulative YoY further to the right.
    """
    result = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"], recursive=False)
        if len(cells) < 3:
            continue
        label = norm(cells[0].get_text(" ", strip=True))
        raw = cells[2].get_text(" ", strip=True).replace("%", "").replace("％", "").strip()
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", raw):
            continue
        value = float(raw)
        if abs(value) > 200:
            continue
        result.setdefault(label, value)
    return result


def select_exact(rows, key):
    target = norm(CELL_TARGETS[key])
    for original_label, value in rows.items():
        # NBS rows often prefix sub-items with “其中：” or “其中”.
        label = re.sub(r"^(其中:|其中：|其中)+", "", original_label).strip()
        # A product row may contain a parenthetical unit, while an industry row
        # must match exactly to avoid confusing a group or subtype with the target.
        if key in COUNT_METRICS:
            match = label == target or label.startswith(target + "(")
        else:
            match = label == target
        if match:
            return value
    return None


def main():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    sources = data.setdefault("sources", {})
    series = data.get("series", [])
    if not isinstance(series, list):
        raise ValueError("Invalid series data")
    try:
        pages = discover()
        index_state = "ok" if pages else "no_matching_new_month"
    except (requests.RequestException, ValueError) as exc:
        pages = {}
        index_state = "failed_" + type(exc).__name__
        print("NBS index unavailable; preserved existing history:", type(exc).__name__)
    added, failed = 0, 0
    checked = []
    # Missing new-month data are not zeros or evidence of publication status.
    today_cn = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))).date()
    last_month = max(sources) if sources else None
    expected_month = None
    if last_month:
        yy, mm = (int(x) for x in last_month.split("-"))
        expected_month = f"{yy + (mm == 12):04d}-{mm % 12 + 1:02d}"
    release_state = (
        "official_release_discovered" if expected_month in pages else
        "official_release_not_verified"
    ) if expected_month and expected_month <= today_cn.strftime("%Y-%m") else "not_due"
    for month, url in sorted(pages.items()):
        if month in sources:
            continue
        try:
            soup = fetch(url)
            rows = industry_table(soup)
            if len(rows) < 20:
                print("Skip", month, "insufficient official table rows:", len(rows))
                checked.append({"period": month, "status": "parse_failed", "rows": len(rows), "source_url": url})
                failed += 1
                continue
            for entry in series:
                key = entry.get("id")
                if key not in CELL_TARGETS:
                    continue
                if any(p.get("period") == month for p in entry.get("points", [])):
                    continue
                value = select_exact(rows, key)
                if value is None:
                    continue
                entry.setdefault("points", []).append({
                    "period": month, "value": value,
                    "source_url": url, "status": "verified",
                })
                entry["points"].sort(key=lambda x: x.get("period", ""))
                added += 1
            sources[month] = url
            checked.append({"period": month, "status": "ok", "rows": len(rows), "source_url": url})
            print("Official NBS month parsed:", month, "table rows", len(rows))
        except (requests.RequestException, ValueError) as exc:
            failed += 1
            checked.append({"period": month, "status": "fetch_failed", "error": type(exc).__name__, "source_url": url})
            print("Skip", month, type(exc).__name__)
        time.sleep(0.3)
    if added:
        data["updated_at"] = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        tmp = PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        tmp.replace(PATH)
    # Save a status heartbeat even when there is no newly published month.
    # This distinguishes healthy no-new-data runs from blocked or broken scrapers.
    status_path = Path("data/industry_update_status.json")
    status_path.write_text(json.dumps({
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "index_state": index_state,
        "next_expected_month": expected_month,
        "release_state": release_state,
        "new_month_pages_found": len(pages),
        "new_points_added": added,
        "failed_pages": failed,
        "monthly_checks": checked,
        "latest_stored_month": max(sources) if sources else None,
        "note": "未核实新月份官方公报时保留旧数据；不可断言尚未发布，也不得补造指标。"
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Industry history: appended", added, "new verified points; skipped sources", failed)


if __name__ == "__main__":
    main()
