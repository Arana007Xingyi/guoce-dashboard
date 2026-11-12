"""Collect official project and funding ANNOUNCEMENT LEADS, never funding facts."""
import datetime
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

CONFIG = Path("data/project_sources.json")
OUTPUT = Path("data/project_discovery.json")
SIGNAL = re.compile(
    r"重大工程|重大项目|重点项目|投资项目|项目清单|项目名单|项目建设|"
    r"项目审批|项目核准|项目批复|立项|开工|竣工|工程进展|建设进展|"
    r"中央基建投资|预算下达|投资预算|资金下达|专项资金|资金分配|"
    r"投资计划|预算执行|财政资金|以工代赈|超长期特别国债|"
    r"项目推介|民间投资项目|投资完成|投资建设|资金到位|开工率|建设项目|"
    r"批复|项目进度|重点工程|工程建设|资金使用|绩效目标|项目投资"
)
NON_SIGNAL = re.compile(
    r"政策解读|答记者问|一图读懂|招聘|考试|人事任免|成品油价格调整|"
    r"审批监管平台|投资项目在线审批监管平台|市场监管平台|登录入口|"
    r"网上办事大厅|首页|服务平台"
)
DATE = re.compile(r"(20\d{2})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})")
MAX_LINKS_PER_SOURCE = 100
MAX_CANDIDATES = 1500
USER_AGENT = "Mozilla/5.0 (compatible; GuoceDashboardOfficialAnnouncementMonitor/1.0)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"})

def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def official(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return (parsed.scheme == "https" and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and (host == "gov.cn" or host.endswith(".gov.cn")))
    except ValueError:
        return False

def date_from(value):
    match = DATE.search(value or "")
    if not match:
        return None
    try:
        return datetime.date(*(int(part) for part in match.groups())).isoformat()
    except ValueError:
        return None

def nearby_date(a):
    node = a
    for _ in range(3):
        if node is None:
            break
        text = " ".join(node.get_text(" ", strip=True).split())
        if len(text) < 320:
            result = date_from(text)
            if result:
                return result
        node = node.parent
    return None

def list_source(source):
    url = source["url"]
    if not official(url):
        raise ValueError("来源地址不属于政府官方域名")
    response = SESSION.get(url, timeout=18, allow_redirects=True)
    response.raise_for_status()
    if not official(response.url):
        raise ValueError("来源跳转到非官方域名")
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    out = []
    seen = set()
    for a in soup.select("a[href]"):
        title = " ".join(a.get_text(" ", strip=True).split())
        if not (8 <= len(title) <= 180):
            continue
        if not SIGNAL.search(title) or NON_SIGNAL.search(title):
            continue
        target = urldefrag(urljoin(response.url, a.get("href", "")))[0]
        if not official(target) or target == url or target in seen:
            continue
        # Skip navigation hubs: only collect public notices or project detail
        # pages, not links to generic service platforms.
        if urlparse(target).path in ("", "/") or re.search(r"/index(?:_\d+)?\.(?:html?|shtml)$",urlparse(target).path):
            continue
        path = urlparse(target).path.lower()
        if path.endswith((".xls", ".xlsx", ".doc", ".docx", ".pdf", ".zip", ".rar", ".png", ".jpg")):
            continue
        # Only monitor 2025+ explicitly dated links when publication date is present.
        # Undated entries remain visibly undated; do not invent date from URL.
        published = nearby_date(a)
        if published and published < "2025-01-01":
            continue
        seen.add(target)
        out.append({
            "id": hashlib.sha256(target.encode("utf-8")).hexdigest()[:20],
            "url": target,
            "title": title,
            "published_at": published,
            "source_id": source["id"],
            "source": source["name"],
            "region": source["region"],
            "agency": source["agency"],
            "category": source["category"],
            "review_status": "unverified_candidate",
            "reason": "由官方公告列表自动发现；尚未核对正文、资金数额和实际执行状态",
        })
        if len(out) >= MAX_LINKS_PER_SOURCE:
            break
    return out

def save_json(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def main():
    sources = json.loads(CONFIG.read_text(encoding="utf-8")).get("sources", [])
    if not OUTPUT.exists():
        existing = {"schema_version": 1, "items": [], "sources": []}
    else:
        existing = json.loads(OUTPUT.read_text(encoding="utf-8"))
    indexed = {x["url"]: x for x in existing.get("items", []) if x.get("url")}
    reports = []
    first_seen = utcnow()
    added = 0
    for source in sources:
        try:
            found = list_source(source)
            for entry in found:
                url = entry["url"]
                if url in indexed:
                    old = indexed[url]
                    old["last_seen_at"] = first_seen
                    # Keep the first source and original discovery date; avoid
                    # recategorizing a previously reviewed candidate.
                    if old.get("review_status") == "unverified_candidate":
                        old.update({k: v for k, v in entry.items()
                                    if k in ("title", "published_at", "source", "region", "agency")})
                else:
                    entry["first_seen_at"] = first_seen
                    entry["last_seen_at"] = first_seen
                    indexed[url] = entry
                    added += 1
            reports.append({"source_id": source["id"], "name": source["name"],
                            "status": "ok" if found else "no_matching_articles",
                            "found": len(found), "checked_at": utcnow()})
            print(f"发现来源：{source['name']}，符合条件 {len(found)} 条")
        except (requests.RequestException, ValueError, KeyError) as e:
            reports.append({"source_id": source["id"], "name": source["name"],
                            "status": "failed", "error": type(e).__name__,
                            "found": 0, "checked_at": utcnow()})
            print(f"来源访问异常：{source['name']}，{type(e).__name__}")
        time.sleep(0.25)

    items = sorted(indexed.values(), key=lambda x: (
        x.get("first_seen_at", ""), x.get("url", "")
    ), reverse=True)[:MAX_CANDIDATES]
    payload = {
        "schema_version": 1,
        "updated_at": utcnow(),
        "methodology": "官方列表页标题级候选；未自动核验项目实际进度、资金金额或拨付。来源仅覆盖当前指定页面，不代表全量官方项目。",
        "sources": reports,
        "items": items,
    }
    save_json(OUTPUT, payload)
    print(f"公告线索采集完成：来源 {len(reports)}，首次发现 {added}，累计 {len(items)}，异常来源 {sum(s['status']=='failed' for s in reports)}")

if __name__ == "__main__":
    main()
