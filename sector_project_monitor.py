"""Monitor named project evidence without silently changing verified milestones.

A government page may be edited without project status changing. This script
tracks source context and suggests a review; only a new verified government
statement may update the curated project dossier.
"""
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

PROJECTS=Path("data/sector_projects.json")
OUTPUT=Path("data/sector_project_monitor.json")
SESSION=requests.Session()
SESSION.headers.update({"User-Agent":"Mozilla/5.0 (GuoceDashboardProjectLifecycle/1.0)",
                        "Accept-Language":"zh-CN,zh;q=0.9"})
STAGES=("latest_stage","total_investment_100m","allocated_100m",
        "paid_100m","spent_100m","investment_progress_pct","acceptance_verified")

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def official(url):
    try:
        u=urlparse(url)
        h=(u.hostname or "").lower()
        return (u.scheme=="https" and u.port in (None,443) and
                not u.username and not u.password and
                (h=="gov.cn" or h.endswith(".gov.cn")))
    except ValueError:
        return False

def hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def evidence_context(text,name):
    # A long annual project list can contain many unrelated changes.
    # Fingerprint the specific project's nearby text, not the full page.
    text=re.sub(r"\s+"," ",text)
    matches=[]
    start=0
    while True:
        pos=text.find(name,start)
        if pos<0:break
        matches.append(text[max(0,pos-50):pos+len(name)+120])
        start=pos+len(name)
        if len(matches)>=8:break
    return hash_text("||".join(matches)) if matches else None

def fetch_page(url):
    r=SESSION.get(url,timeout=15,allow_redirects=True)
    r.raise_for_status()
    if not official(r.url):raise ValueError("not_official_redirect")
    r.encoding=r.apparent_encoding or "utf-8"
    soup=BeautifulSoup(r.text,"html.parser")
    for tag in soup.select("script,style,noscript,nav,header,footer"):
        tag.decompose()
    root=soup.select_one("#zoom") or soup.select_one(".TRS_Editor") or soup.select_one("article") or soup
    return " ".join(root.get_text(" ",strip=True).split())

def main():
    source=json.loads(PROJECTS.read_text(encoding="utf-8"))
    data=json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    tracked=data.setdefault("tracked",{})
    events=data.setdefault("events",[])
    at=stamp()
    pages={}
    reviewed=0
    for project in source.get("records",[]):
        pid=project["id"]
        url=project["source_url"]
        if not official(url):continue
        current={k:project.get(k) for k in STAGES}
        previous=tracked.get(pid,{})
        if not previous:
            events.append({
                "id":"baseline:"+pid,"project_id":pid,"type":"verified_baseline",
                "observed_at":at,"title":project["name"],"source_url":url,
                "description":"建立官方项目档案初始基线；不是新增开工或付款事件"})
        elif previous.get("fields")!=current:
            diff=[k for k in STAGES if previous.get("fields",{}).get(k)!=current.get(k)]
            token=hash_text(pid+json.dumps(current,ensure_ascii=False,sort_keys=True))[:16]
            if not any(e.get("id")==token for e in events):
                events.append({
                    "id":token,"project_id":pid,"type":"curated_record_changed",
                    "observed_at":at,"title":project["name"],"source_url":url,
                    "description":"项目档案字段变化，需复核出处："+",".join(diff)})
        state=tracked.setdefault(pid,{})
        state["fields"]=current
        state["source_url"]=url
        if url not in pages:
            try:pages[url]=("ok",fetch_page(url))
            except (requests.RequestException,ValueError) as e:
                pages[url]=("failed",type(e).__name__)
        status,page=pages[url]
        state["checked_at"]=at
        if status!="ok":
            state["check_status"]="source_unavailable"
            state["error"]=page
            continue
        digest=evidence_context(page,project["name"])
        if not digest:
            # Do not invent an update when a page's text extractor fails.
            state["check_status"]="name_not_found"
            continue
        if state.get("context_digest") and state["context_digest"]!=digest:
            token=hash_text(pid+digest)[:16]
            if not any(e.get("id")==token for e in events):
                events.append({
                    "id":token,"project_id":pid,"type":"source_context_changed",
                    "observed_at":at,"title":project["name"],"source_url":url,
                    "description":"政府原文中该项目邻近文本变化，待核实；不自动更新开工、支付或竣工状态"})
        state["context_digest"]=digest
        state["check_status"]="ok"
        reviewed+=1
    data.update({
        "schema_version":1,"updated_at":at,
        "methodology":"官方原文项目邻近文本变动仅触发人工核验，不自动改动任何阶段、投资或支付数字；计划与实际进展永远分开。",
        "events":events[-1000:],
    })
    tmp=OUTPUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUTPUT)
    print("产业项目监测：",len(tracked),"个档案",reviewed,"个来源文本匹配",
          "待核查事件",sum(e.get("type")=="source_context_changed" for e in events))

if __name__=="__main__":
    main()
