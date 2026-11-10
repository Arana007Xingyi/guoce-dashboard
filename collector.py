
import json
import re
import hashlib
import datetime
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

import requests
from bs4 import BeautifulSoup

OUT = Path("data/policies.json")

SOURCES = [
    ("生态环境部·中央文件",
     "https://www.mee.gov.cn/zcwj/zyygwj/", "中央"),
    ("生态环境部·国务院文件",
     "https://www.mee.gov.cn/zcwj/gwywj/", "国务院"),
    ("国务院·政策文件",
     "https://www.gov.cn/zhengce/index.htm", "国务院"),
    ("工业和信息化部·政策通知",
     "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/index.html", "部委"),
    ("国家发展改革委·政策通知",
     "https://www.ndrc.gov.cn/xxgk/zcfb/tz/", "部委"),
    ("国家发展改革委·规章",
     "https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/", "部委"),
]

KEYWORDS = [
    "意见", "通知", "方案", "规划", "条例",
    "办法", "措施", "决定", "规定", "批复",
    "公告", "实施细则", "行动计划"
]

TOPICS = {
    "人工智能": ["人工智能", "算力", "大模型", "智能体", "数据要素", "数字经济"],
    "先进制造": ["先进制造", "智能制造", "工业母机", "机器人", "装备制造", "集成电路", "半导体", "产业基础"],
    "医疗健康": ["医疗", "医药", "健康", "养老", "生物医药", "医疗器械", "中医药"],
    "绿色能源": ["新能源", "可再生能源", "光伏", "储能", "生态环境", "节能", "碳排放", "绿色低碳"],
    "消费民生": ["消费", "就业", "民生", "住房", "社会保障", "居民收入"],
    "财政金融": ["财政", "税收", "金融", "融资", "专项债", "预算内投资", "贴息"],
    "民营经济": ["民营", "中小企业", "营商环境", "专精特新", "小微企业"],
    "低空经济": ["低空经济", "无人机", "通用航空", "低空飞行"],
    "科技创新": ["科技创新", "技术创新", "研发", "成果转化", "新质生产力"],
    "现代农业": ["农业", "乡村振兴", "粮食安全", "农机", "高标准农田"]
}

DATE = re.compile(
    r"(20\d{2})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})\s*日?"
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "zh-CN,zh;q=0.9"
})

# 每次运行最多尝试抓取的正文数量
MAX_ARTICLES = 40

# 单篇正文最大保存字符数
MAX_CONTENT = 12000

# 仅允许政府官方域名
DOMAINS = (
    "gov.cn",
    "ndrc.gov.cn",
    "miit.gov.cn",
    "mee.gov.cn"
)


def clean(value):
    return " ".join(str(value or "").split())


def official_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and (
            parsed.port is None
            or parsed.port == 443
        )
        and any(
            host == d or host.endswith("." + d)
            for d in DOMAINS
        )
    )


def get_date(text):
    match = DATE.search(text or "")
    if not match:
        return None

    try:
        return datetime.date(
            *map(int, match.groups())
        ).isoformat()
    except ValueError:
        return None


def fetch(url):
    if not official_url(url):
        raise ValueError("非允许的官方地址")

    response = SESSION.get(
        url,
        timeout=25,
        allow_redirects=True
    )
    response.raise_for_status()

    if not official_url(response.url):
        raise ValueError("跳转到了非允许域名")

    if response.apparent_encoding:
        response.encoding = response.apparent_encoding

    return response


def find_date_near_link(a):
    node = a

    for _ in range(4):
        if node is None:
            break

        for tag in node.select(
            "time, .date, .time, [class*=date], [class*=time]"
        ):
            date = get_date(
                tag.get_text(" ", strip=True)
            )
            if date:
                return date

        text = clean(
            node.get_text(" ", strip=True)
        )

        if len(text) <= 600:
            date = get_date(text)
            if date:
                return date

        node = node.parent

    return None


def classify(title):
    topics = [
        topic
        for topic, words in TOPICS.items()
        if any(word in title for word in words)
    ]
    return topics or ["其他政策"]


def collect(name, url, scope):
    response = fetch(url)

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    found = []
    seen = set()

    today = datetime.datetime.now(
        datetime.timezone(
            datetime.timedelta(hours=8)
        )
    ).date()

    candidate_count = 0
    missing_date = 0

    for a in soup.select("a[href]"):
        title = clean(
            a.get_text(" ", strip=True)
        )

        link = urldefrag(
            urljoin(response.url, a.get("href", ""))
        )[0]

        if not 10 <= len(title) <= 180:
            continue

        if not any(k in title for k in KEYWORDS):
            continue

        if not official_url(link):
            continue

        if link.rstrip("/") == url.rstrip("/"):
            continue

        if link in seen:
            continue

        seen.add(link)
        candidate_count += 1

        date = find_date_near_link(a)

        if not date:
            missing_date += 1
            continue

        d = datetime.date.fromisoformat(date)

        if d > today + datetime.timedelta(days=1):
            continue

        if d.year < 2015:
            continue

        found.append({
            "id": hashlib.sha256(
                link.encode("utf-8")
            ).hexdigest()[:16],
            "title": title,
            "date": date,
            "source": name,
            "scope": scope,
            "url": link,
            "topics": classify(title)
        })

    print(
        "候选链接:", candidate_count,
        "缺少日期:", missing_date,
        "有效政策:", len(found)
    )

    return found


def extract_content(url):
    """
    提取HTML正文。
    无法可靠识别时返回状态，不伪造内容。
    """
    try:
        response = fetch(url)

        content_type = response.headers.get(
            "Content-Type", ""
        ).lower()

        if (
            "application/pdf" in content_type
            or urlparse(response.url).path.lower().endswith(".pdf")
        ):
            return "", "pdf_attachment"

        if (
            "text/html" not in content_type
            and "application/xhtml+xml" not in content_type
        ):
            return "", "unsupported_format"

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # 优先查找政府网站常见正文容器
        selectors = [
            "#UCAP-CONTENT",
            "#zoom",
            "#Zoom",
            ".TRS_Editor",
            ".pages_content",
            ".article-content",
            ".article_content",
            ".content-article",
            ".detail-content",
            ".detail_content",
            ".article",
            "article"
        ]

        candidates = []

        for selector in selectors:
            for node in soup.select(selector):
                # 复制节点，避免修改原始文档
                part = BeautifulSoup(
                    str(node), "html.parser"
                )

                for bad in part.select(
                    "script, style, nav, footer, "
                    "header, form, iframe, "
                    ".share, .pagination"
                ):
                    bad.decompose()

                text = clean(
                    part.get_text(" ", strip=True)
                )

                if len(text) >= 150:
                    candidates.append(text)

        if not candidates:
            return "", "content_not_found"

        # 选择最长的候选正文
        content = max(candidates, key=len)

        # 内容过短时不视为成功
        if len(content) < 150:
            return "", "content_too_short"

        return content[:MAX_CONTENT], "success"

    except Exception as e:
        print("正文提取异常:", str(e)[:120])
        return "", "fetch_failed"


def enrich_articles(items):
    """
    优先抓取最新政策。
    已成功保存的正文不重复抓取。
    """
    attempted = 0
    successful = 0

    for item in items:
        if attempted >= MAX_ARTICLES:
            break

        if item.get("content_status") == "success" and item.get("content"):
            continue

        if not official_url(item.get("url", "")):
            continue

        attempted += 1

        content, status = extract_content(
            item["url"]
        )

        item["content_status"] = status

        if status == "success":
            item["content"] = content
            item["content_length"] = len(content)
            item["content_fetched_at"] = (
                datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
            )
            successful += 1

        print(
            "正文:", attempted,
            status,
            clean(item.get("title", ""))[:35]
        )

        time.sleep(0.3)

    print(
        "本次正文尝试:", attempted,
        "成功:", successful
    )


def main():
    old = []

    if OUT.exists():
        try:
            previous = json.loads(
                OUT.read_text(encoding="utf-8")
            )
            old = previous.get("items", [])
        except (ValueError, OSError) as e:
            print("旧数据读取异常:", e)

    merged = {
        p["url"]: p
        for p in old
        if isinstance(p, dict) and p.get("url")
    }

    ok = []
    failed = []

    for name, url, scope in SOURCES:
        print("正在采集:", name)

        try:
            records = collect(name, url, scope)

            if not records:
                raise ValueError(
                    "未识别到符合条件且带日期的政策"
                )

            ok.append({
                "source": name,
                "count": len(records)
            })

            for record in records:
                existing = merged.get(
                    record["url"], {}
                )

                # 新元数据覆盖旧元数据，
                # 但保留已有正文与采集状态
                merged[record["url"]] = {
                    **existing,
                    **record
                }

            print(
                "成功:", name,
                len(records), "条"
            )

        except Exception as e:
            failed.append({
                "source": name,
                "error": str(e)[:200]
            })
            print("失败:", name, str(e))

        time.sleep(1)

    if not ok and not old:
        raise RuntimeError(
            "全部数据源失败且无历史数据"
        )

    items = sorted(
        merged.values(),
        key=lambda p: p.get("date", ""),
        reverse=True
    )[:1500]

    print("开始采集政策正文")

    enrich_articles(items)

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    payload = {
        "updated_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "sources_ok": ok,
        "sources_failed": failed,
        "items": items
    }

    # 先写临时文件，再替换正式文件
    temp = OUT.with_suffix(".json.tmp")

    temp.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    temp.replace(OUT)

    total_content = sum(
        1 for p in items
        if p.get("content_status") == "success"
        and p.get("content")
    )

    print("-----------------------")
    print("累计政策:", len(items))
    print("成功来源:", len(ok))
    print("异常来源:", len(failed))
    print("已保存正文:", total_content)
    print("-----------------------")


if __name__ == "__main__":
    main()
