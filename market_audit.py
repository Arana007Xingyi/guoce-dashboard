"""Audit CSI market PE history against official CSI P/E1 and P/E2 .xls files.

Independent official XLS corroboration is required before calling peg PE1/PE2.
Never claim PE-TTM, calculate official PB, or infer turnover money units.
Runs after market_history_update.py; generates a small auditable JSON.
"""
import datetime as dt
import io
import json
import math
from pathlib import Path

import requests
import xlrd

MARKET = Path("data/market_history.json")
CONFIG = Path("data/market_config.json")
OUT = Path("data/market_audit.json")
ROOT = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/"
MIN_MATCHES = 5
REL_TOL = 0.015
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"})

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def positive(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    except (ValueError, TypeError):
        return None

def parse_date(value):
    # xlrd often exposes 20261111 as the float 20261111.0
    if isinstance(value,(int,float)) and float(value).is_integer():
        x=str(int(value))
    else:
        x=str(value).strip()
    if x.isdigit() and len(x) == 8:
        try: return dt.datetime.strptime(x,"%Y%m%d").date().isoformat()
        except ValueError: return None
    if len(x) >= 10:
        try: return dt.date.fromisoformat(x[:10]).isoformat()
        except ValueError: return None
    return None

def official_indicator_rows(data):
    """CSI indicator columns: trade date, index code, names(4), P/E1, P/E2, D/P1, D/P2.
    Interpret as official PE by equity definition, NOT automatically TTM.
    """
    book = xlrd.open_workbook(file_contents=data)
    results = {}
    for sh in book.sheets():
        for i in range(sh.nrows):
            row = sh.row_values(i)
            if len(row) < 8:
                continue
            day = parse_date(row[0])
            if not day:
                continue
            pe1, pe2 = positive(row[6]), positive(row[7])
            if pe1 is not None or pe2 is not None:
                results[day] = {"pe1":pe1,"pe2":pe2}
    if not results:
        raise ValueError("Official CSI xls has no recognized PE1 or PE2 observations")
    return results

def compare_index(points, official):
    """Only claim an identified PE field if 5+ dates match and the alternative
    official field is not similarly consistent.
    """
    overlaps = []
    for p in points:
        day = p.get("date")
        peg = positive(p.get("pe_ttm"))
        if peg is None or day not in official:
            continue
        row = official[day]
        if row["pe1"] is not None and row["pe2"] is not None:
            overlaps.append((peg,row["pe1"],row["pe2"]))
    result = {"common_valid_dates":len(overlaps),"pe_field_status":"unverified",
              "identified_field":None,"pe1_match_count":0,"pe2_match_count":0}
    for peg,pe1,pe2 in overlaps:
        result["pe1_match_count"] += abs(peg-pe1)/pe1 <= REL_TOL
        result["pe2_match_count"] += abs(peg-pe2)/pe2 <= REL_TOL
    if len(overlaps) < MIN_MATCHES:
        result["pe_field_status"] = "insufficient_official_overlap"
    else:
        a = result["pe1_match_count"] / len(overlaps)
        b = result["pe2_match_count"] / len(overlaps)
        if b >= .9 and a < .9:
            result.update({"pe_field_status":"matched_official_pe2","identified_field":"P/E2（计算用股本）"})
        elif a >= .9 and b < .9:
            result.update({"pe_field_status":"matched_official_pe1","identified_field":"P/E1（总股本）"})
        elif a >= .9 and b >= .9:
            result["pe_field_status"] = "ambiguous_pe1_pe2"
        else:
            result["pe_field_status"] = "mismatch_official_valuation"
    return result

def local_audit(instrument):
    pts = instrument.get("points", [])
    good_pe = [p for p in pts if positive(p.get("pe_ttm")) is not None]
    dates = [p.get("date") for p in good_pe if parse_date(p.get("date"))]
    latest = max(dates) if dates else None
    values = [positive(p.get("pe_ttm")) for p in good_pe]
    official_pct = instrument.get("pe_percentile")
    return {
        "id": instrument.get("id"), "code":instrument.get("code"), "name":instrument.get("name"),
        "kind": instrument.get("kind"), "price_rows":len(pts), "valid_pe_rows":len(good_pe),
        "pe_coverage_pct": round(100*len(good_pe)/len(pts),1) if pts else 0,
        "first_pe_date":min(dates) if dates else None, "last_pe_date":latest,
        "latest_pe_source_value":values[-1] if values else None,
        "historical_percentile_exploratory": official_pct if isinstance(official_pct,(int,float)) else None,
        "official_pe_ttm_verified":False, "turnover_unit_verified":False,
        "pe_field_status":"not_checked", "identified_field":None,
        "warnings":["接口原字段 peg 的P/E统计口径需对照中证官方估值文件",
                    "历史分位只代表同一指数接口的样本内分布，非估值买卖信号",
                    "amount金额单位没有官方验证，禁止按元或亿元换算"],
    }

def main():
    data=json.loads(MARKET.read_text(encoding="utf-8"))
    configured=json.loads(CONFIG.read_text(encoding="utf-8"))
    ids={x["id"]:x for x in configured.get("indices",[])}
    results=[]
    for instrument in data.get("instruments",[]):
        if not isinstance(instrument,dict): continue
        item=local_audit(instrument)
        if item["kind"] != "index" or item["id"] not in ids:
            item["pe_field_status"]="not_applicable_etf"
            results.append(item);continue
        url=ROOT+ids[item["id"]]["code"]+"indicator.xls"
        item["official_indicator_url"]=url
        try:
            r=SESSION.get(url,timeout=12,allow_redirects=False)
            r.raise_for_status()
            if len(r.content)>12_000_000: raise ValueError("Oversize official xls")
            official=official_indicator_rows(r.content)
            item.update(compare_index(instrument.get("points",[]),official))
            item["latest_official_indicator_date"]=max(official)
            print(item["id"],item["pe_field_status"],"PE rows",item["valid_pe_rows"],
                  "overlap",item["common_valid_dates"])
        except (requests.RequestException, ValueError, xlrd.XLRDError, IndexError) as exc:
            item["pe_field_status"]="official_indicator_check_failed"
            item["official_check_error"]=type(exc).__name__
            print(item["id"],"官方估值对照失败",type(exc).__name__)
        results.append(item)
    out={"schema_version":1,"checked_at":now(),"methodology":
      "CSI peg 字段与中证官方 indicator.xls 的P/E1和P/E2按同日比对；最少5个有效重合日期且90%以上匹配方可声明对应股本口径。即使匹配也不代表已证实TTM；成交额单位未认证，ETF不生成PE。不会写回或修改行情历史。",
      "source":"https://www.csindex.com.cn/","instruments":results}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PE审计完成",len(results),"指数",
          sum(x["kind"]=="index" for x in results),
          "PE字段明确对应",
          sum(x.get("identified_field") is not None for x in results))

if __name__ == "__main__":
    main()
