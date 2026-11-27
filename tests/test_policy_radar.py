import unittest
from policy_radar import classify
from finance_policy_sources import allowed, parse_date

class RadarTests(unittest.TestCase):
    def signals(self,title,body="",url="https://www.csrc.gov.cn/csrc/c106311/c7508374/content.shtml"):
        return {x["signal"] for x in classify(dict(title=title,content=body,content_status="success",url=url,date="2024-09-24"))}
    def test_924(self):
        # Condensed fixture from CSRC 2024-09-24 press conference transcript.
        self.assertTrue({"joint","rate","tools","longterm"} <= self.signals("国新办举行新闻发布会", "人民银行、金融监管总局、证监会；降低存款准备金率；互换便利；保险资金入市"))
    def test_huijin(self):
        self.assertIn("huijin",self.signals("中央汇金公司将持续加大ETF增持力度",url="https://www3.xinhuanet.com/20240206/92872317bc2d44e8a4d5a07ba1272734/c.html"))
    def test_macro(self):
        self.assertIn("macro",self.signals("政治局会议部署增量政策"))
    def test_untrusted(self):
        self.assertFalse(self.signals("央行降准",url="https://gov.cn.example.com/news"))
    def test_no_signal(self):
        self.assertFalse(self.signals("普通工作会议"))
    def test_status(self):
        self.assertEqual(classify(dict(title="降准",url="https://www.pbc.gov.cn/"))[0]["status"],"待核实")
    def test_date(self):
        self.assertIsNone(parse_date("09-24"))
        self.assertIsNone(parse_date("2024-02-30"))
        self.assertEqual(parse_date("2024年9月24日").isoformat(),"2024-09-24")
    def test_url(self):
        self.assertFalse(allowed("http://www.gov.cn/"))
        self.assertFalse(allowed("https://evil@www.gov.cn/"))

if __name__ == "__main__": unittest.main()
