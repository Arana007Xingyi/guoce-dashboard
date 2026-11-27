"""Generate candidate policy signals from the existing official policy feed."""
import json
import re
from difflib import SequenceMatcher
from finance_policy_sources import collect, allowed
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

RULES = [
 ("joint", "三部门联合政策", "S", [["人民银行","央行"],["金融监管总局","金融监督管理总局"],["证监会"],["发布会","联合"]]),
 ("rate", "降准降息", "S", [["降准","降息","下调存款准备金率","下调政策利率","降低存款准备金率","降低政策利率"]]),
 ("tools", "资本市场支持工具", "S", [["互换便利","回购增持再贷款","专项再贷款","资本市场支持工具"]]),
 ("huijin", "中央汇金增持ETF", "S", [["中央汇金"],["增持","买入"],["ETF","交易型开放式指数基金"]]),
 ("macro", "重大宏观增量政策", "S", [["政治局"],["增量政策","稳住股市","稳股市","提振资本市场信心","提振资本市场"]]),
 ("longterm", "中长期资金入市", "A", [["保险资金","社保基金","中长期资金"],["入市","权益投资","股票投资"]]),
]

def classify(item):
    url = urlparse(item.get("url",""))
    host = url.hostname or ""
    if not allowed(item.get("url","")):
        return []
    title = item.get("title","")
    body = item.get("content","") if item.get("content_status") == "success" else ""
    # A match is a candidate, never a confirmation of magnitude or implementation.
    text = title + " " + body
    return [{"signal": key, "name": name, "candidate_level": level,
             "status": "待核实", "title": title, "url": item["url"],
             "date": item.get("date"), "source": item.get("source",""),
             "collection_channel":item.get("collection_channel","existing_feed"),
             "matched": [next(w for w in group if w in text) for group in groups]}
            for key,name,level,groups in RULES
            if all(any(w in text for w in group) for group in groups)]

def corroborate(events):
    # Similar titles on the same day are leads, not independent confirmation.
    for e in events:
        e["related_reports"]=[]
        a=re.sub(r"[^\w]","",e["title"])
        for other in events:
            if e["signal"]!=other["signal"] or e["date"]!=other["date"] or e["url"]==other["url"]: continue
            if e["source"]==other["source"]: continue
            b=re.sub(r"[^\w]","",other["title"])
            if SequenceMatcher(None,a,b).ratio()>=0.75:
                e["related_reports"].append({"source":other["source"],"url":other["url"],"title":other["title"]})
        e["verification"]="多来源相似报道，独立性待核实" if e["related_reports"] else "单来源候选，待核实"
    return events

def main():
    source = json.loads(Path("data/policies.json").read_text(encoding="utf-8"))
    now = datetime.now(timezone(timedelta(hours=8)))
    cutoff = (now.date()-timedelta(days=7)).isoformat()
    fresh, failures, health = collect(now.date(), classify)
    out = Path("data/policy_radar.json")
    previous = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    events = {(e["signal"],e["url"]):e for e in previous.get("events",[]) if cutoff <= e["date"] <= now.date().isoformat()}
    for item in source.get("items",[]) + fresh:
        if not cutoff <= (item.get("date") or "") <= now.date().isoformat():
            continue
        for event in classify(item):
            events[(event["signal"],event["url"])] = event
    payload = {"updated_at":now.isoformat(),"source_updated_at":source.get("updated_at"),
               "finance_sources":[h for h in health if h.get("channel")=="news"],
               "reprint_sources":[h for h in health if h.get("channel")=="reprint"],
               "document_sources":[h for h in health if h.get("channel")=="documents"],
               "document_candidates":[{"title":i["title"],"url":i["url"],"date":i["date"],"source":i["source"]} for i in fresh if i.get("collection_channel")=="policy_library"],
               "coverage":{"parsed":sum(h["status"]=="parsed" for h in health if h.get("channel")=="news"),"total":sum(h.get("channel")=="news" for h in health),"degraded":sum(h["status"]=="parsed" for h in health if h.get("channel")=="news")*2<sum(h.get("channel")=="news" for h in health)},
               "sources_failed":source.get("sources_failed",[]) + failures,
               "signals":[{"id":k,"name":n,"candidate_level":l} for k,n,l,_ in RULES],
               "events":sorted(corroborate(list(events.values())),key=lambda e:e["date"],reverse=True)}
    print(json.dumps(health, ensure_ascii=False))
    out = Path("data/policy_radar.json")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(out)

if __name__ == "__main__":
    main()
