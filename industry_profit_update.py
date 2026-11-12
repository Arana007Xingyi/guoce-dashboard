"""Verify monthly cumulative industrial industry profit history from NBS Table 3.

Only accepts rows with industry name + six numeric data cells in official NBS
2026 1-N month profit tables. Never creates interpolated or monthly profit YOY.
"""
import datetime as dt
import json
import re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

OUT=Path("data/industry_profit_history.json")
STATUS=Path("data/industry_profit_status.json")
SOURCES={
 "2026-06":"https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202607/t20260727_1964194.html",
 "2026-07":"https://www.stats.gov.cn/sj/zxfb/202608/t20260827_1965126.html",
 "2026-08":"https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202609/t20260928_1965425.html",
 "2026-09":"https://www.stats.gov.cn/sj/zxfb/202610/t20261027_1965697.html"
}
SESSION=requests.Session()
SESSION.headers.update({"User-Agent":"Mozilla/5.0","Accept-Language":"zh-CN,zh;q=0.9"})

def compact(s):
    return re.sub(r"[\s\u3000]+","",str(s or "")).replace("、","").replace("，","")

def numeric(text):
    v=str(text).replace(",","").replace("%","").strip()
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?",v):
        return float(v)
    return None

def profit_rows(soup):
    """Table 3 row: Industry, revenue, yoy, cost, yoy, profit, profit yoy.
    Some pages include extra columns/cells; last two are used only when
    six preceding numeric cells and industry label match expected.
    """
    found={}
    for tr in soup.find_all("tr"):
        cells=tr.find_all(["td","th"],recursive=False)
        if len(cells)<7:
            continue
        name=compact(cells[0].get_text(" ",strip=True))
        vals=[numeric(c.get_text(" ",strip=True)) for c in cells[1:7]]
        if not name or len(vals)!=6 or any(x is None for x in vals):
            continue
        if vals[-2]<0 or abs(vals[-1])>1000:
            continue
        found.setdefault(name,{"cumulative_profit_100m":vals[-2],
                               "cumulative_yoy_pct":vals[-1]})
    return found

def main():
    raw=json.loads(OUT.read_text(encoding="utf-8"))
    industries=raw.get("industries",[])
    targets={compact(x["industry"]):x for x in industries}
    reports=[]
    changes=0
    for period,url in SOURCES.items():
        count=0
        state="ok"
        try:
            r=SESSION.get(url,timeout=18,allow_redirects=False)
            r.raise_for_status()
            if not r.url.startswith("https://www.stats.gov.cn/"):
                raise ValueError("非国家统计局地址")
            r.encoding=r.apparent_encoding or "utf-8"
            rows=profit_rows(BeautifulSoup(r.text,"html.parser"))
            if len(rows)<15:
                raise ValueError("官方分行业财务表未有效解析")
            for key,industry in targets.items():
                row=rows.get(key)
                if row is None: continue
                if any(x["period"]==period for x in industry.get("points",[])):
                    count+=1;continue
                industry.setdefault("points",[]).append({
                    "period":period,**row,"source_url":url,"status":"verified"})
                industry["points"].sort(key=lambda x:x["period"])
                changes+=1;count+=1
            if count==0:
                state="no_target_industries"
            raw.setdefault("sources",{})[period]=url
        except (requests.RequestException,ValueError) as exc:
            state="failed_"+type(exc).__name__
        reports.append({"period":period,"status":state,"matched_industries":count,
                        "source_url":url})
        print("行业利润历史",period,state,"行业",count)
    raw["updated_at"]=dt.datetime.now(dt.timezone.utc).isoformat()
    temp=OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(raw,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
    STATUS.write_text(json.dumps({
        "updated_at":raw["updated_at"],"new_points":changes,"reports":reports,
        "note":"月份是1—N月累计同比，不是当月同比；未匹配到的数据保留缺口。"
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("行业利润数据新增",changes,"条；累计",
          sum(len(x["points"]) for x in industries),"条")

if __name__=="__main__":
    main()
