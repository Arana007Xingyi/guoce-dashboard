import json, re, hashlib
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from finance_policy_sources import fetch, allowed
from policy_radar import classify
CASES = [
 ("924","2024-09-24","https://www.csrc.gov.cn/csrc/c106311/c7508374/content.shtml",["joint","rate","tools","longterm"],"official_transcript"),
 ("926","2024-09-26","https://www.mofcom.gov.cn/syxwfb/art/2024/art_e17e66e23bfb492e99804b1dfa620321.html",["macro","rate","longterm"],"official_site_reprint"),
 ("huijin","2024-02-06","https://www.csrc.gov.cn/csrc/c100028/c7462111/content.shtml",["huijin"],"regulator_response"),
 ("longterm","2024-09-26","https://www.csrc.gov.cn/csrc/c100028/c7508981/content.shtml",["longterm"],"official_release"),
]
def replay():
    results=[]
    for key,date,url,expected,kind in CASES:
        row=dict(id=key,date=date,url=url,expected=expected,kind=kind)
        try:
            r=fetch(url)
            soup=BeautifulSoup(r.text,"html.parser")
            for tag in soup.select("script,style,nav,footer,header"): tag.decompose()
            body=soup.get_text(" ",strip=True)
            body=re.sub(r"E\s*T\s*F","ETF",body,flags=re.I)
            title=soup.title.get_text(strip=True) if soup.title else ""
            actual=sorted({e["signal"] for e in classify(dict(title=title,content=body,content_status="success",url=r.url,date=date))})
            row.update(final_url=r.url,http_status=r.status_code,sha256=hashlib.sha256(r.content).hexdigest(),actual=actual,missing=sorted(set(expected)-set(actual)),title=title)
            row["pass"]=not row["missing"]
        except Exception as exc: row.update(error=str(exc),**{"pass":False})
        results.append(row)
    return results

def discover_json():
    from playwright.sync_api import sync_playwright
    pages=["https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemId=928&itemPId=928&itemUrl=ItemListRightList.html","https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary?t=zhengcelibrary","https://www.ndrc.gov.cn/xxgk/wjk/index.html?tab=qt"]
    report=[]
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for url in pages:
            page=browser.new_page()
            found=[]
            page.route("**/*",lambda route: route.continue_() if allowed(route.request.url) and route.request.resource_type not in ("image","font","media") else route.abort())
            def observe(response):
                if not allowed(response.url) or "json" not in response.headers.get("content-type","").lower(): return
                try:
                    obj=response.json()
                    found.append(dict(url=response.url,status=response.status,method=response.request.method,keys=list(obj)[:20] if isinstance(obj,dict) else [],type=type(obj).__name__))
                except Exception: pass
            page.on("response",observe)
            row=dict(page=url,candidates=found)
            try:
                page.goto(url,wait_until="domcontentloaded",timeout=20000)
                page.wait_for_timeout(5000)
            except Exception as exc: row["error"]=str(exc)
            report.append(row)
            page.close()
        browser.close()
    return report

if __name__=="__main__":
    result=dict(time=datetime.now(timezone.utc).isoformat(),historical=replay(),json_candidates=discover_json())
    Path("final-audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))
    assert all(r["pass"] for r in result["historical"]), "Historical replay failed"
