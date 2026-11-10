import json
import re
import hashlib
import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path("data/policies.json")
SOURCES = [
    ("生态环境部·中央文件", "https://www.mee.gov.cn/zcwj/zyygwj/", "中央"),
    ("生态环境部·国务院文件", "https://www.mee.gov.cn/zcwj/gwywj/", "国务院"),
]
KEYWORDS = ["意见", "通知", "方案", "规划", "条例", "办法", "措施", "决定", "规定"]
TOPICS = {
    "人工智能": ["人工智能", "算力", "数字", "数据"],
    "先进制造": ["制造", "工业", "机器人", "装备"],
    "医疗健康": ["医疗", "医药", "健康", "养老"],
    "绿色能源": ["能源", "绿色", "生态", "环保", "碳"],
    "消费民生": ["消费", "就业", "民生"],
    "财政金融": ["财政", "税收", "金融", "融资"],
    "民营经济": ["民营", "中小企业", "营商"],
}
DATE = re.compile(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})")

def collect(name, url, scope):
    r = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")
    found = []
    for a in soup.select("a[href]"):
        title = " ".join(a.get_text(" ", strip=True).split())
        link = urljoin(url, a.get("href", ""))
        if not 12 <= len(title) <= 130:
            continue
        if not any(k in title for k in KEYWORDS):
            continue
        if urlparse(link).netloc != urlparse(url).netloc:
            continue
        match = DATE.search(a.parent.get_text(" ", strip=True)[:260])
        if not match:
            continue
        try:
            date = datetime.date(*map(int, match.groups())).isoformat()
        except ValueError:
            continue
        topics = [t for t, words in TOPICS.items() if any(w in title for w in words)]
        found.append({
            "id": hashlib.sha256(link.encode()).hexdigest()[:16],
            "title": title,
            "date": date,
            "source": name,
            "scope": scope,
            "url": link,
            "topics": topics or ["其他政策"],
        })
    return found

def main():
    old = []
    if OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
    merged = {p["url"]: p for p in old}
    ok, failed = [], []
    for name, url, scope in SOURCES:
        try:
            records = collect(name, url, scope)
            if not records:
                raise ValueError("未识别到带日期的政策")
            ok.append({"source": name, "count": len(records)})
            for record in records:
                merged[record["url"]] = record
        except Exception as e:
            failed.append({"source": name, "error": str(e)[:160]})
    if not ok and not old:
        raise RuntimeError("没有成功采集的数据，请检查官方来源")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources_ok": ok,
        "sources_failed": failed,
        "items": sorted(merged.values(), key=lambda x: x["date"], reverse=True)[:1500],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已保存政策：", len(payload["items"]))

if __name__ == "__main__":
    main()
