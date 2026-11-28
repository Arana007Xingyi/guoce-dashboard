import json, subprocess, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
server=subprocess.Popen([sys.executable,"-m","http.server","8765","--bind","127.0.0.1"])
rows=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch()
        for name,w,h in [("desktop",1440,900),("phone",390,844),("ipad",1024,1366)]:
            page=browser.new_page(viewport={"width":w,"height":h})
            errors=[]
            page.on("pageerror",lambda err: errors.append(str(err)))
            page.goto("http://127.0.0.1:8765/index.html",wait_until="domcontentloaded")
            page.wait_for_function("document.querySelector('#radarHomeStatus') && !document.querySelector('#radarHomeStatus').textContent.includes('正在读取')",timeout=15000)
            panel=page.locator("#policyRadarHome")
            panel.scroll_into_view_if_needed()
            panel.screenshot(path=f"frontend-{name}.png")
            text=panel.inner_text()
            links=panel.locator("a").evaluate_all("(items)=>items.map(a=>a.href)")
            rows.append(dict(device=name,text=text,links=links,errors=errors,pass_check="更新时间" in text and "政策文件通道" in text and "雷达数据不可用" not in text and not errors))
            page.close()
        browser.close()
finally:
    server.terminate()
    Path("frontend-audit.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
assert len(rows)==3 and all(r["pass_check"] for r in rows)
