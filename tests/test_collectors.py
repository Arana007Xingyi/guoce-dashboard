"""Offline parser and idempotence regression tests. No internet, no AI tokens."""
import datetime
import unittest

from bs4 import BeautifulSoup

import industry_history_update as ind
import market_history_update as market


class TestNbsMonthly(unittest.TestCase):
    def test_title_period(self):
        self.assertEqual(ind.month_from_title("2026年10月份规模以上工业增加值增长"), "2026-10"
                         if ind.CURRENT_MONTH >= "2026-10" else None)
        self.assertIsNone(ind.month_from_title("2025年12月份规模以上工业增加值增长"))

    def test_exact_official_table_and_monthly_column(self):
        # NBS's leftmost row label can contain 其中： and production units;
        # column 3 must be monthly YOY, not column 5 cumulative YOY.
        html = """
        <table>
          <tr><td>其中：高技术制造业</td><td>…</td><td>14.1</td><td>…</td><td>13.3</td></tr>
          <tr><td>专用设备制造业</td><td>…</td><td>10.0</td><td>…</td><td>8.2</td></tr>
          <tr><td>工业机器人（套）</td><td>110702</td><td>28.1</td><td>537689</td><td>28.0</td></tr>
          <tr><td>其中：新能源汽车（万辆）</td><td>162.4</td><td>29.4</td><td>739.9</td><td>6.0</td></tr>
          <tr><td>太阳能电池（光伏电池）（万千瓦）</td><td>7420</td><td>-8.4</td><td>38118</td><td>-14.2</td></tr>
          <tr><td>集成电路（亿块）</td><td>517</td><td>18.8</td><td>2798</td><td>23.1</td></tr>
        </table>"""
        rows = ind.industry_table(BeautifulSoup(html, "html.parser"))
        self.assertEqual(ind.select_exact(rows, "hi_tech"), 14.1)
        self.assertEqual(ind.select_exact(rows, "special_machinery"), 10.0)
        self.assertEqual(ind.select_exact(rows, "robot_output"), 28.1)
        self.assertEqual(ind.select_exact(rows, "nev_output"), 29.4)
        self.assertEqual(ind.select_exact(rows, "ic_output"), 18.8)
        self.assertEqual(ind.select_exact(rows, "solar"), -8.4)
        self.assertIsNone(ind.select_exact(rows, "food"))


class TestMarket(unittest.TestCase):
    def test_index_history_pe_and_absent_pb(self):
        sample = {"data": [
            {"tradeDate": "20261110", "indexCode": "000300", "close": "4059.11",
             "peg": "13.13", "amount": "99832"},
            {"tradeDate": "20261110", "indexCode": "000905", "close": "9999", "peg": "60"},
        ]}
        rows = market.parse_csi(sample, "000300")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-11-10")
        self.assertEqual(rows[0]["pe_ttm"], 13.13)
        self.assertIsNone(rows[0]["pb"])

    def test_free_etf_fallback_never_invents_turnover(self):
        sample = {"data": {"sh510300": {
            "day": [["2026-11-10", "4.1", "4.12", "4.15", "4.09", "20000"]]
        }}}
        row = market.parse_tencent_etf(sample, "510300")[0]
        self.assertEqual(row["date"], "2026-11-10")
        self.assertEqual(row["close"], 4.12)
        self.assertEqual(row["volume_unit"], "hands")
        self.assertIsNone(row["turnover"])
        self.assertIsNone(row["pe_ttm"])
        self.assertIsNone(row["pb"])

    def test_etf_history_no_invented_turnover_or_pe(self):
        sample = [{"date": "2026-11-10", "close": 4.012, "volume": 10000.0}]
        row = market.parse_etf(sample)[0]
        self.assertEqual(row["close"], 4.012)
        self.assertIsNone(row["turnover"])
        self.assertIsNone(row["pe_ttm"])
        self.assertIsNone(row["pb"])

    def test_merge_retains_old_values_on_new_null(self):
        old = [{"date": "2026-11-10", "close": 1.23, "pe_ttm": 14.2, "pb": None}]
        new = [{"date": "2026-11-10", "close": 1.24, "pe_ttm": None, "pb": None}]
        merged, updated = market.merge(old, new)
        self.assertEqual(len(merged), 1)
        self.assertEqual(updated, 1)
        self.assertEqual(merged[0]["close"], 1.24)
        self.assertEqual(merged[0]["pe_ttm"], 14.2)

    def test_percentile_minimum_750_and_3y(self):
        base = datetime.date(2023, 1, 1)
        short = [{"date": (base + datetime.timedelta(days=i)).isoformat(), "pe_ttm": 11}
                 for i in range(100)]
        self.assertIsNone(market.percentile(short))
        valid = [{"date": (base + datetime.timedelta(days=i * 2)).isoformat(), "pe_ttm": 10 + (i % 10)}
                 for i in range(750)]
        self.assertIsNotNone(market.percentile(valid))


if __name__ == "__main__":
    unittest.main()
