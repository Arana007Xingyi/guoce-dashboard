"""Extract grounded details from government implementation announcement pages.

This is DOCUMENT METADATA enrichment, not automated financial fact verification.
It never infers funding amounts, payment, completion or project approvals.
"""
import datetime as dt
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup

DISCOVERY=Path("data/project_discovery.json")
OUTPUT=Path("data/project_notice_details.json")
SESSION=requests.Session()
SESSION.headers.update({"User-Agent":"Mozilla/5.0 (compatible; GuoceDashboardEvidenceResearch/1.0)","Accept-Language":"zh-CN,zh;q=0.9"})
MAX_PER_RUN=25
DOCNO=re.compile(r"(?:财建|发改投资|发改基础|发改高技|沪发改|粤发改|苏发改)[〔\[]\s*20\d{2}\s*[〕\]]\s*\d+号")
PURPOSE=re.compile(r"(?:专项用于|用于|支持)(.{4,85}?)(?:。|；|，|,)")
NOTICES=re.compile(r"中央基建投资|中央预算内投资|预算指标|投资预算")
ATTACH=re.compile(r"附件|下载|xls|xlsx|docx|pdf",re.I)

def official(url):
    u=urlparse(url)
    host=(u.hostname or "").lower()
    return u.scheme=="https" and (host=="gov.cn" or host.endswith(".gov.cn")) and not u.username and not u.password and u.port in (None,443)

def extract(url):
    if not official(url): raise ValueError("非政府官方网页")
    resp=SESSION.get(url,timeout=13,allow_redirects=True)
    resp.raise_for_status()
    if not official(resp.url): raise ValueError("跳转后非政府网站")
    resp.encoding=resp.apparent_encoding or "utf-8"
    soup=BeautifulSoup(resp.text,"html.parser")
    for tag in soup.select("script,style,nav,header,footer,noscript"):
        tag.decompose()
    root=soup.select_one("#zoom") or soup.select_one(".TRS_Editor") or soup.select_one(".article") or soup.select_one("article") or soup
    text=" ".join(root.get_text(" ",strip=True).split())
    if len(text)<80:raise ValueError("正文过短或动态加载")
    doc=DOCNO.search(text)
    purpose=PURPOSE.search(text)
    attachments=[]
    for a in root.select("a[href]"):
        label=" ".join(a.get_text(" ",strip=True).split())[:110]
        href=a.get("href","")
        target=urljoin(resp.url,href)
        if not official(target):continue
        if (ATTACH.search(label) or re.search(r"\.(?:pdf|xls|xlsx|doc|docx)(?:$|\?)",href,re.I)) and target!=url:
            attachments.append({"title":label or "文件附件","url":target})
    attachments=list({a["url"]:a for a in attachments}.values())[:8]
    kind=("中央基建预算下达文件" if NOTICES.search(text) else
          "重大项目或投资工作公告")
    return {
        "document_number":doc.group(0) if doc else None,
        "purpose_excerpt":purpose.group(1).strip()[:85] if purpose else None,
        "document_kind":kind,
        "mentions_attachments":bool(attachments or "附件" in text),
        "attachments":attachments,
        "source_url":url,
        "source_hash":hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "caution":"正文已提取文件元信息；并未验证附件中项目或具体金额，不代表资金实际拨付/支付或项目竣工。"
    }

def main():
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    discovered=json.loads(DISCOVERY.read_text(encoding="utf-8")).get("items",[])
    stored=json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {}
    details={x["url"]:x for x in stored.get("items",[]) if x.get("url")}
    count=0;ok=0;failed=0
    for item in discovered:
        url=item.get("url","")
        if not official(url) or url in details or count>=MAX_PER_RUN:
            continue
        count+=1
        try:
            result=extract(url)
            result.update({"url":url,"checked_at":now,"status":"extracted"})
            details[url]=result
            ok+=1
        except (requests.RequestException,ValueError) as err:
            details[url]={"url":url,"checked_at":now,"status":"unavailable",
                          "error":type(err).__name__,
                          "caution":"未能提取网页正文，不能认为文件没有附件或没有资金"}
            failed+=1
        time.sleep(.1)
    payload={
        "schema_version":1,"updated_at":now,
        "methodology":"仅提取官方网页文号、资金用途短语及官方附件链接。未阅读附件明细，不自动填项目金额或认定拨款支付。若提取失败，显式展示未核验。",
        "items":list(details.values())[-1200:],
        "last_run":{"checked":count,"extracted":ok,"failed":failed}
    }
    temp=OUTPUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUTPUT)
    print("项目公告正文增强：检查",count,"成功",ok,"失败",failed,"累计",len(details))

if __name__=="__main__":
    main()
