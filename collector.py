
import json
import re
import hashlib
import datetime
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

OUT = Path("data/policies.json")

# 官方政策来源
SOURCES = [
    (
        "生态环境部·中央文件",
        "https://www.mee.gov.cn/zcwj/zyygwj/",
        "中央"
    ),
    (
        "生态环境部·国务院文件",
        "https://www.mee.gov.cn/zcwj/gwywj/",
        "国务院"
    ),
    (
        "国务院·政策文件",
        "https://www.gov.cn/zhengce/index.htm",
        "国务院"
    ),
    (
        "工业和信息化部·政策通知",
        "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/index.html",
        "部委"
    ),
    (
        "国家发展改革委·政策通知",
        "https://www.ndrc.gov.cn/xxgk/zcfb/tz/",
        "部委"
    ),
    (
        "国家发展改革委·规章",
        "https://www.ndrc.gov.cn/xxgk/zcfb/fzggwl/",
        "部委"
    ),
]

KEYWORDS = [
    "意见", "通知", "方案", "规划", "条例",
    "办法", "措施", "决定", "规定", "批复",
    "公告", "实施细则", "行动计划"
]

TOPICS = {
    "人工智能": [
        "人工智能", "算力", "大模型", "智能体",
        "数据要素", "数字经济"
    ],
    "先进制造": [
        "先进制造", "智能制造", "工业母机",
        "机器人", "装备制造", "集成电路",
        "半导体", "产业基础"
    ],
    "医疗健康": [
        "医疗", "医药", "健康", "养老",
        "生物医药", "医疗器械", "中医药"
    ],
    "绿色能源": [
        "新能源", "可再生能源", "光伏",
        "储能", "生态环境", "节能",
        "碳排放", "绿色低碳"
    ],
    "消费民生": [
        "消费", "就业", "民生", "住房",
        "社会保障", "居民收入"
    ],
    "财政金融": [
        "财政", "税收", "金融", "融资",
        "专项债", "预算内投资", "贴息"
    ],
    "民营经济": [
        "民营", "中小企业", "营商环境",
        "专精特新", "小微企业"
    ],
    "低空经济": [
        "低空经济", "无人机", "通用航空",
        "低空飞行"
    ],
    "科技创新": [
        "科技创新", "技术创新", "研发",
        "成果转化", "新质生产力"
    ],
    "现代农业": [
        "农业", "乡村振兴", "粮食安全",
        "农机", "高标准农田"
    ]
}

DATE = re.compile(
    r"(20\d{2})\s*[-年./]\s*"
    r"(\d{1,2})\s*[-月./]\s*"
    r"(\d{1,2})\s*日?"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; "
        "PolicyResearchBot/1.0)"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean(value):
    return " ".join(str(value or "").split())


def get_date(text):
    match = DATE.search(text)
    if not match:
        return None

    try:
        return datetime.date(
            *map(int, match.groups())
        ).isoformat()
    except ValueError:
        return None


def official_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        domains = [
            "gov.cn",
            "ndrc.gov.cn",
            "miit.gov.cn",
            "mee.gov.cn"
        ]

        return (
            parsed.scheme == "https"
            and any(
                host == d or host.endswith("." + d)
                for d in domains
            )
        )
    except Exception:
        return False


def find_date_near_link(a):
    # 从链接向上查找最多四层容器
    node = a

    for _ in range(4):
        if node is None:
            break

        # 优先检查时间标签
        for tag in node.select("time, .date, .time"):
            date = get_date(tag.get_text(" ", strip=True))
            if date:
                return date

        text = clean(node.get_text(" ", strip=True))

        # 避免整个页面日期混入单条记录
        if len(text) <= 600:
            date = get_date(text)
            if date:
                return date

        node = node.parent

    return None


def collect(name, url, scope):
    response = SESSION.get(
        url,
        timeout=30
    )
    response.raise_for_status()

    if response.apparent_encoding:
        response.encoding = response.apparent_encoding

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    found = []
    seen = set()

    for a in soup.select("a[href]"):
        title = clean(a.get_text(" ", strip=True))
        link = urljoin(url, a.get("href", ""))

        if not 10 <= len(title) <= 180:
            continue

        if not any(k in title for k in KEYWORDS):
            continue

        if not official_url(link):
            continue

        # 排除栏目首页及明显非正文链接
        if link.rstrip("/") == url.rstrip("/"):
            continue

        if link in seen:
            continue

        date = find_date_near_link(a)

        # 不猜测发布日期
        if not date:
            continue

        # 排除未来日期和明显不合理的历史日期
        today = datetime.date.today()
        d = datetime.date.fromisoformat(date)

        if d > today + datetime.timedelta(days=1):
            continue

        if d.year < 2015:
            continue

        topics = [
            topic
            for topic, words in TOPICS.items()
            if any(word in title for word in words)
        ]

        record = {
            "id": hashlib.sha256(
                link.encode("utf-8")
            ).hexdigest()[:16],
            "title": title,
            "date": date,
            "source": name,
            "scope": scope,
            "url": link,
            "topics": topics or ["其他政策"]
        }

        found.append(record)
        seen.add(link)

    return found


def main():
    old = []

    if OUT.exists():
        try:
            previous = json.loads(
                OUT.read_text(encoding="utf-8")
            )
            old = previous.get("items", [])
        except (ValueError, OSError) as e:
            print("旧数据读取异常：", e)

    merged = {
        p["url"]: p
        for p in old
        if isinstance(p, dict) and p.get("url")
    }

    ok = []
    failed = []

    for name, url, scope in SOURCES:
        print("正在采集：", name)

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
                merged[record["url"]] = record

            print(
                "成功：",
                name,
                len(records),
                "条"
            )

        except Exception as e:
            failed.append({
                "source": name,
                "error": str(e)[:200]
            })
            print("失败：", name, str(e))

        time.sleep(1)

    if not ok and not old:
        raise RuntimeError(
            "全部数据源失败且无历史数据，"
            "请检查官方页面结构和网络连接"
        )

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    items = sorted(
        merged.values(),
        key=lambda p: p.get("date", ""),
        reverse=True
    )[:1500]

    payload = {
        "updated_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "sources_ok": ok,
        "sources_failed": failed,
        "items": items
    }

    OUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    print("-----------------------")
    print("累计政策：", len(items))
    print("成功来源：", len(ok))
    print("异常来源：", len(failed))
    print("-----------------------")


if __name__ == "__main__":
    main()
