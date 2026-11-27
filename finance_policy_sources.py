"""Best-effort official finance news collector; never infer missing years."""
import re
import json
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
        s.mount("https://",HTTPAdapter(max_retries=Retry(total=1,backoff_factor=0.5,status_forcelist=[429,500,502,503,504],allowed_methods=["GET"],respect_retry_after_header=False)))
        LOCAL.session=s
    return LOCAL.session
from bs4 import BeautifulSoup

SOURCES = [
 ("央行",["https://www.pbc.gov.cn/"]),
 ("证监会",["https://www.csrc.gov.cn/csrc/xwfb/index.shtml","https://www.csrc.gov.cn/"]),
 ("金融监管总局",["https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemId=928&itemPId=928&itemUrl=ItemListRightList.html","https://www.nfra.gov.cn/cn/view/pages/zhengwuxinxi/zhengwuxinxi.html"]),
 ("中央汇金",["https://www.huijin-inv.cn/","https://www.china-inv.cn/"]),
 ("中国政府网",["https://www.gov.cn/zhengce/","https://www.gov.cn/yaowen/","https://www.gov.cn/"]),
 ("新华社",["https://www.news.cn/fortune/","https://www.news.cn/"]),
 ("央视新闻",["https://news.cctv.com/china/","https://jingji.cctv.com/","https://news.cctv.com/"]),
]
DOCUMENT_SOURCES = [
 ("国务院政策文件库",["https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?t=zhengcelibrary"]),
 ("发展改革委文件库",["https://www.ndrc.gov.cn/xxgk/zcfb/tz/","https://www.ndrc.gov.cn/xxgk/zcfb/gg/","https://www.ndrc.gov.cn/xxgk/wjk/index.html?tab=qt"]),
 ("国务院文件专栏",["https://www.beijing.gov.cn/zhengce/gwywj/"]),
]
REPRINT_SOURCES = [("中央汇金权威转载",["https://www.news.cn/fortune/","https://www.news.cn/","https://news.cctv.com/"])]
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

def render_listing(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        try:
            page=browser.new_page()
            page.route("**/*",lambda route: route.continue_() if allowed(route.request.url) and route.request.resource_type not in ("image","media","font") else route.abort())
            response=page.goto(url,wait_until="domcontentloaded",timeout=15000)
            if not response or response.status>=400 or not allowed(page.url):
                raise ValueError("动态页面访问失败")
            page.wait_for_timeout(2500)
            return BeautifulSoup(page.content(),"html.parser"),page.url
        finally:
            browser.close()

def article_link(url):
    if not allowed(url): return False
    p=urlparse(url)
    path=p.path.lower()
    if p.hostname=="www.ndrc.gov.cn":
        return bool(re.search(r"/t20\d{6}_\d+\.html$",path))
    if p.hostname=="www.nfra.gov.cn":
        return "itemdetail" in path and "docid=" in p.query.lower()
    return True

def publication_dates(doc):
    dates=[]
    for m in doc.select("meta[content]"):
        key=(m.get("name") or m.get("property") or m.get("itemprop") or "").lower()
        if key in ("pubdate","publishdate","pubtime","article:published_time","datepublished","publication_date","publish_time","contentdate"):
            dates.append(m["content"])
    for script in doc.select('script[type="application/ld+json"]'):
        try:
            obj=json.loads(script.string or "")
            nodes=obj if isinstance(obj,list) else [obj]
            for node in nodes:
                if isinstance(node,dict):
                    dates.append(str(node.get("datePublished","")))
                    for sub in node.get("@graph",[]):
                        if isinstance(sub,dict): dates.append(str(sub.get("datePublished","")))
        except (ValueError,TypeError): pass
    for n in doc.select("time,.date,.pubtime,.publish-time,.time,.pub-date,.source-time,#pubtime,.article-time,.articleTime,.publishDate,#publishTime"):
        dates.append(n.get("datetime") or n.get_text(" ",strip=True))
    return dates

def collect_one(name,url,today,classify,documents=False):
    items=[]; failed=[]; health=[]
    started=time.monotonic()
    for name,url in [(name,url)]:
        scanned=dated=0; seen=set(); start=len(items); errors=len(failed)
        try:
            r=fetch(url); soup=BeautifulSoup(r.text,"html.parser")
            seen=set(); scanned=0; dated=0
            base=r.url
            mode="html"
            if len(soup.select("a[href]"))<15:
                try:
                    soup,base=render_listing(url); mode="browser"
                except Exception as exc:
                    failed.append({"source":name,"error":"动态采集："+str(exc)[:120]})
            for a in soup.select("a[href]"):
                title=" ".join((a.get("title") or a.get_text(" ",strip=True)).split())
                link=urldefrag(urljoin(base,a["href"]))[0]
                if not 6<=len(title)<=180 or link in seen or not article_link(link): continue
                seen.add(link)
                item={"title":title,"url":link,"source":name}
                if name=="中央汇金权威转载" and "中央汇金" not in title: continue
                if not documents and name not in ("中国政府网","央视新闻","金融监管总局") and not classify(item) and not any(k in title for k in ("发布会","政治局会议","金融支持","货币政策","资本市场","增量政策","金融监管","保险资金","中央汇金","证券","金融政策")): continue
                if scanned>=10 or time.monotonic()-started>65: break
                scanned+=1
                try:
                    article=fetch(link); doc=BeautifulSoup(article.text,"html.parser")
                    dates=publication_dates(doc)
                    if not any(parse_date(t) for t in dates) and name in ("金融监管总局","发展改革委文件库"):
                        try:
                            doc,_=render_listing(link); dates=publication_dates(doc)
                        except Exception: pass
                    dates += [a.parent.get_text(" ",strip=True)[:250]]
                    d=next((v for t in dates if (v:=parse_date(t))),None)
                    if not d: continue
                    dated+=1
                    if not today-timedelta(days=7)<=d<=today: continue
                    content_nodes=doc.select("#UCAP-CONTENT, .TRS_Editor, .article-content, .article_content, article, #zoom")
                    if content_nodes:
                        item.update(content=max((n.get_text(" ",strip=True) for n in content_nodes),key=len)[:50000],content_status="success")
                    item.update(date=d.isoformat(),url=article.url,collection_channel="policy_library" if documents else ("reprint" if name=="中央汇金权威转载" else "news"))
                    if not documents and not classify(item): continue
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
            items,errors,health=collect_one(name,url,today,classify,source in DOCUMENT_SOURCES)
            all_items.extend(items); all_errors.extend(errors)
            h=health[0]; h["endpoint"]=url; attempts.append(h)
            if h["status"]=="parsed": break
        best=max(attempts,key=lambda h:(h["status"]=="parsed",h["dated"],h["links"]))
        best=dict(best,attempts=attempts,channel="documents" if source in DOCUMENT_SOURCES else ("reprint" if source in REPRINT_SOURCES else "news"))
        return all_items,all_errors,[best]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(run,SOURCES+DOCUMENT_SOURCES+REPRINT_SOURCES))
    return tuple([v for result in results for v in result[i]] for i in range(3))
