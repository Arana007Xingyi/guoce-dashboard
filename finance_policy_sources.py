"""Best-effort official finance news collector; never infer missing years."""
import re
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup

SOURCES = [
 ("央行","https://www.pbc.gov.cn/"),
 ("证监会","https://www.csrc.gov.cn/csrc/xwfb/index.shtml"),
 ("金融监管总局","https://www.nfra.gov.cn/cn/view/pages/xinwenzixun/xinwenzixun.html"),
 ("中央汇金","https://www.huijin-inv.cn/huijin-inv/Corporate_History/index.shtml"),
 ("中国政府网","https://www.gov.cn/yaowen/"),
]
HOSTS = ("gov.cn","huijin-inv.cn","china-inv.cn","news.cn")
DATE = re.compile(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})")
def allowed(url):
    p=urlparse(url)
    return p.scheme=="https" and not p.username and not p.password and p.port in (None,443) and any(p.hostname==h or (p.hostname or "").endswith("."+h) for h in HOSTS)

def fetch(url):
    for _ in range(5):
        if not allowed(url): raise ValueError("非允许域名")
        r=requests.get(url,timeout=15,allow_redirects=False,headers={"User-Agent":"Mozilla/5.0 PolicyRadar"})
        if r.is_redirect:
            url=urljoin(url,r.headers["Location"])
            continue
        r.raise_for_status()
        r.encoding=r.apparent_encoding or "utf-8"
        return r
    raise ValueError("重定向过多")

def parse_date(text):
    m=DATE.search(text)
    if m:
        try: return date(*map(int,m.groups()))
        except ValueError: pass
    return None

def collect(today, classify):
    items=[]; failed=[]
    for name,url in SOURCES:
        try:
            r=fetch(url); soup=BeautifulSoup(r.text,"html.parser")
            seen=set(); scanned=0; dated=0
            for a in soup.select("a[href]"):
                title=" ".join(a.get_text(" ",strip=True).split())
                link=urldefrag(urljoin(r.url,a["href"]))[0]
                if not 10<=len(title)<=180 or link in seen or not allowed(link): continue
                seen.add(link)
                item={"title":title,"url":link,"source":name}
                if not classify(item): continue
                if scanned>=30: break
                scanned+=1
                try:
                    article=fetch(link); doc=BeautifulSoup(article.text,"html.parser")
                    dates=[m.get("content","") for m in doc.select('meta[name="PubDate"],meta[name="publishdate"],meta[property="article:published_time"]')]
                    dates += [n.get_text(" ",strip=True) for n in doc.select("time,.date,.pubtime,.publish-time")]
                    dates += [a.parent.get_text(" ",strip=True)[:250]]
                    d=next((v for t in dates if (v:=parse_date(t))),None)
                    if not d: continue
                    dated+=1
                    if not today-timedelta(days=7)<=d<=today: continue
                    item.update(date=d.isoformat(),url=article.url)
                    items.append(item)
                except Exception as exc: failed.append({"source":name,"error":str(exc)[:160]})
            if not seen or (scanned and not dated):
                raise ValueError("未识别有效链接或完整发布日期")
        except Exception as exc: failed.append({"source":name,"error":str(exc)[:160]})
    return items,failed
