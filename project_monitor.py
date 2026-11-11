"""Evidence-first project/funding monitoring. Does not call any AI model."""
import datetime
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

LEDGER = Path("data/project_funding.json")
POLICIES = Path("data/policies.json")
OUTPUT = Path("data/project_monitor.json")
FIELDS = (
    "name", "stage", "as_of", "planned_100m", "allocated_100m",
    "paid_100m", "spent_100m", "annual_investment_completion_pct", "source_url"
)
TERMS = {
    "p-sanxia-channel": ["三峡水运新通道", "三峡新通道"],
    "p-guangzhou-airport": ["广州新机场"],
    "p-shangbaishi": ["上白石", "水利枢纽"],
    "p-xinzang-rail": ["新藏铁路"],
    "f-liangzhong": ["两重", "6064.6"],
    "f-tradein": ["以旧换新", "1250亿元"],
    "f-health": ["医疗服务与保障能力提升", "352.1亿元"],
    "f-central-investment": ["中央预算内投资", "7550亿元"],
    "m-yunnan": ["云南省级重大项目", "1866个", "93.76%"],
}
SIGNALS = re.compile(
    r"拨付|下达|预算执行|财政支出|专项债|特别国债|中央预算内投资|"
    r"重大项目|项目进展|项目开工|建设进度|竣工|验收|投资完成|"
    r"投资计划|资金安排|补助资金|贴息|工程进展|以旧换新|"
    r"实施方案|资金分配|资金使用"
)
EXCLUDE = re.compile(r"答记者问|政策解读|一图读懂|新闻发布会")
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "GuoceDashboardEvidenceMonitor/1.0 (+https://github.com/Arana007Xingyi/guoce-dashboard)",
    "Accept-Language": "zh-CN,zh;q=0.9",
})

def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def load(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def official(url):
    try:
        u = urlparse(url)
        h = (u.hostname or "").lower()
        return (u.scheme == "https" and not u.username and not u.password
                and u.port in (None, 443)
                and (h == "gov.cn" or h.endswith(".gov.cn")))
    except ValueError:
        return False

def fingerprint(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def article_text(response):
    soup = BeautifulSoup(response.text, "html.parser")
    for bad in soup.select("script,style,noscript,nav,header,footer"):
        bad.decompose()
    candidates = [
        soup.select_one("#zoom"), soup.select_one(".TRS_Editor"),
        soup.select_one(".article"), soup.select_one("article"),
        soup.select_one("main"), soup.select_one("body"),
    ]
    texts = [" ".join(el.get_text(" ", strip=True).split())
             for el in candidates if el is not None]
    return max(texts, key=len)[:75000] if texts else ""

def context_hash(text, words):
    relevant = []
    for word in words:
        start = 0
        while True:
            at = text.find(word, start)
            if at < 0:
                break
            relevant.append(text[max(0, at - 120):at + len(word) + 180])
            start = at + len(word)
            if len(relevant) >= 35:
                break
    if not relevant:
        return None
    # Deduplicate contexts. Only changes near the target record are monitored.
    return fingerprint("\n".join(sorted(set(relevant))))

def record_fields(row):
    return {k: row.get(k) for k in FIELDS}

def add_event(output, kind, record, details, when):
    event = {
        "id": fingerprint(kind + "|" + record["id"] + "|" + details)[:20],
        "kind": kind, "record_id": record["id"],
        "title": record.get("name", ""), "stage": record.get("stage"),
        "as_of": record.get("as_of"), "observed_at": when,
        "description": details, "source_url": record.get("source_url"),
        "verification": "needs_review" if kind != "verified_baseline" else "initial_verified"
    }
    if not any(item.get("id") == event["id"] for item in output["history"]):
        output["history"].append(event)

def main():
    when = stamp()
    ledger = load(LEDGER, {"records": []}).get("records", [])
    policies = load(POLICIES, {"items": []}).get("items", [])
    if not isinstance(ledger, list) or not isinstance(policies, list):
        raise ValueError("台账或政策列表格式不正确")
    output = load(OUTPUT, {})
    output.setdefault("schema_version", 1)
    output.setdefault("methodology", "自动监测只记录待核查来源变化，不自动认定资金下达或项目完成")
    output.setdefault("records", {})
    output.setdefault("history", [])
    output.setdefault("candidates", [])
    output.setdefault("seen_policy_urls", [])
    seen = set(output["seen_policy_urls"])
    old_candidates = {row.get("url") for row in output["candidates"]}
    new_candidates = 0
    new_events = 0

    # Discover new official policy documents without automatically asserting
    # that they belong to a project, that financing occurred, or a stage changed.
    for policy in policies:
        url = policy.get("url", "")
        if not official(url) or url in seen:
            continue
        seen.add(url)
        title = str(policy.get("title", "")).strip()
        if not SIGNALS.search(title) or EXCLUDE.search(title) or url in old_candidates:
            continue
        related = [
            row["id"] for row in ledger
            if any(term in title for term in TERMS.get(row["id"], [row.get("name", "")]))
        ]
        output["candidates"].append({
            "id": fingerprint(url)[:20], "url": url,
            "title": title, "date": policy.get("date"),
            "source": policy.get("source"),
            "detected_at": when, "record_ids": related,
            "review_status": "unverified_candidate",
            "reason": "新采集的官方文件标题符合项目或资金关键词；具体执行事实未核实",
        })
        new_candidates += 1

    # Preserve source link + first baseline; never overwrite official/curated
    # money figures as a result of a scraped page changing.
    unique_urls = {}
    for row in ledger:
        rid = row.get("id")
        if not rid or not official(row.get("source_url", "")):
            continue
        state = output["records"].setdefault(rid, {})
        current_fields = record_fields(row)
        previous_fields = state.get("fields")
        if previous_fields is None:
            add_event(output, "verified_baseline", row,
                      "初始台账证据基线（并非项目完工或资金到账证明）", when)
        elif previous_fields != current_fields:
            changed = [k for k in FIELDS
                       if previous_fields.get(k) != current_fields.get(k)]
            add_event(output, "ledger_revision", row,
                      "台账字段修订，需复核官方证据：" + "、".join(changed), when)
            new_events += 1
        state["fields"] = current_fields
        unique_urls.setdefault(row["source_url"], []).append(row)

    # Fetch each unique official page once; track text near named items only.
    page_cache = {}
    for url in list(unique_urls)[:20]:
        try:
            response = SESSION.get(url, timeout=15, allow_redirects=True)
            response.raise_for_status()
            if not official(response.url):
                raise ValueError("非政府域名跳转")
            response.encoding = response.apparent_encoding or "utf-8"
            page_cache[url] = ("ok", article_text(response))
        except (requests.RequestException, ValueError) as exc:
            page_cache[url] = ("failed", type(exc).__name__)
        time.sleep(0.15)

    for url, rows in unique_urls.items():
        status, text = page_cache.get(url, ("not_checked", "超出本轮监测上限"))
        for row in rows:
            state = output["records"][row["id"]]
            state["last_checked_at"] = when
            if status != "ok":
                state["check_status"] = status
                state["last_error"] = text
                continue
            digest = context_hash(text, TERMS.get(row["id"], [row.get("name", "")]))
            if digest is None:
                state["check_status"] = "target_text_not_found"
                state["last_error"] = "网页中未定位到与该项目/资金相关的文本；不判定为变化"
                continue
            previous = state.get("source_hash")
            if previous and previous != digest:
                add_event(output, "source_context_changed", row,
                          "官方来源页面相关文本发生变化，待人工核查；不表示开工、拨款或竣工", when)
                new_events += 1
            state["source_hash"] = digest
            state["check_status"] = "ok"
            state["last_error"] = None

    output["updated_at"] = when
    output["seen_policy_urls"] = sorted(seen)[-5000:]
    output["candidates"] = output["candidates"][-500:]
    output["history"] = output["history"][-1000:]
    save(OUTPUT, output)
    print("项目资金监测完成：项目/资金", len(ledger),
          "新官方文件线索", new_candidates,
          "新变化事件", new_events,
          "累计线索", len(output["candidates"]),
          "历史事件", len(output["history"]))
    print("来源监测状态：", {
        status: sum(1 for x in output["records"].values()
                    if x.get("check_status") == status)
        for status in set(x.get("check_status") for x in output["records"].values())
    })

if __name__ == "__main__":
    main()
