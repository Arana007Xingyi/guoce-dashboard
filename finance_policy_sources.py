"""Best-effort official finance news collector; never infer missing years."""
import re
from datetime import date, timedelta
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor
import time
import threading
LOCAL = threading.local()
def session():
    if not hasattr(LOCAL,"session"):
        s=requests.Session()
        s.mount("https://",HTTPAdapter(max_retries=Retry(total=1,backoff_factor=0.5,status_forcelist=[429,500,502,503,504],allowed_methods=["GET"])))
        LOCAL.session=s
    return LOCAL.session
from bs4 import BeautifulSoup

SOURCES = [
 ("央行",["https://www.pbc.gov.cn/"]),
 ("证监会",["https://www.csrc.gov.cn/csrc/xwfb/index.shtml","https://www.csrc.gov.cn/"]),
 ("金融监管总局",["https://www.nfra.gov.cn/cn/view/pages/xinwenzixun/xinwenzixun.html","https://www.nfra.gov.cn/"]),
 ("中央汇金",["https://www.huijin-inv.cn/","https://www.china-inv.cn/"]),
 ("中国政府网",["https://www.gov.cn/yaowen/","https://www.gov.cn/"]),
 ("新华社",["https://www.news.cn/fortune/","https://www.news.cn/"]),
 ("央视新闻",["https://news.cctv.com/","https://jingji.cctv.com/"]),
]
HOSTS = ("gov.cn","huijin-inv.cn","china-inv.cn","news.cn","xinhuanet.com","cctv.com")
DATE = re.compile(r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})")
def allowed(url):
    p=urlparse(url)
    return p.scheme=="https" and not p.username and not p.password and p.port in (None,443) and any(p.hostname==h or (p.hostname or "").endswith("."+h) for h in HOSTS)

def fetch(url):
    for _ in range(5):
        if not allowed(url): raise ValueError("非允许域名")
        r=session().get(url,timeout=(5,8),allow_redirects=False,headers={"User-Agent":"Mozilla/5.0 PolicyRadar"})
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

def collect_one(name,url,today,classify):
    items=[]; failed=[]; health=[]
    started=time.monotonic()
    for name,url in [(name,url)]:
        scanned=dated=0; seen=set(); start=len(items); errors=len(failed)
        try:
            r=fetch(url); soup=BeautifulSoup(r.text,"html.parser")
            seen=set(); scanned=0; dated=0
            for a in soup.select("a[href]"):
                title=" ".join(a.get_text(" ",strip=True).split())
                link=urldefrag(urljoin(r.url,a["href"]))[0]
                if not 10<=len(title)<=180 or link in seen or not allowed(link): continue
                seen.add(link)
                item={"title":title,"url":link,"source":name}
                if not classify(item) and not any(k in title for k in ("发布会","政治局会议","金融支持","货币政策","资本市场","增量政策")): continue
                if scanned>=10 or time.monotonic()-started>65: break
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
                    content_nodes=doc.select("#UCAP-CONTENT, .TRS_Editor, .article-content, .article_content, article, #zoom")
                    if content_nodes:
                        item.update(content=max((n.get_text(" ",strip=True) for n in content_nodes),key=len)[:50000],content_status="success")
                    item.update(date=d.isoformat(),url=article.url)
                    if not classify(item): continue
                    items.append(item)
                except Exception as exc: failed.append({"source":name,"error":str(exc)[:160]})
            if not seen or (scanned and not dated):
                raise ValueError("未识别有效链接或完整发布日期")
        except Exception as exc: failed.append({"source":name,"error":str(exc)[:160]})
        health.append({"source":name,"links":len(seen),"attempted":scanned,"dated":dated,"matches":len(items)-start,"errors":len(failed)-errors,"status":"error" if len(failed)>errors else ("parsed" if dated else "unverified")})
    return items,failed,health

def collect(today,classify):
    def run(source):
        name,urls=source
        all_items=[]; all_errors=[]; attempts=[]
        for url in urls:
            items,errors,health=collect_one(name,url,today,classify)
            all_items.extend(items); all_errors.extend(errors)
            h=health[0]; h["endpoint"]=url; attempts.append(h)
            if h["status"]=="parsed": break
        best=max(attempts,key=lambda h:(h["status"]=="parsed",h["dated"]))
        best=dict(best,attempts=attempts)
        return all_items,all_errors,[best]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(run,SOURCES))
    return tuple([v for result in results for v in result[i]] for i in range(3))
