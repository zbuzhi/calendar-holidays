import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manage", ROOT / "scripts/manage.py")
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class DataChecks(unittest.TestCase):
    def setUp(self):
        self.plan = manage.read(ROOT / "plans/2026.json")

    def test_2026_matches_official_notice(self):
        days = manage.expand(self.plan)["days"]
        self.assertEqual(len(days), 39)
        self.assertEqual(sum(day["isOffDay"] for day in days), 33)
        lookup = {day["date"]: day for day in days}
        self.assertEqual({d["date"] for d in days if not d["isOffDay"]}, {
            "2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"})
        for start, end in [("2026-01-01", "2026-01-03"), ("2026-02-15", "2026-02-23"),
                           ("2026-04-04", "2026-04-06"), ("2026-05-01", "2026-05-05"),
                           ("2026-06-19", "2026-06-21"), ("2026-09-25", "2026-09-27"),
                           ("2026-10-01", "2026-10-07")]:
            day, last = manage.parse_date(start), manage.parse_date(end)
            while day <= last:
                self.assertTrue(lookup[day.isoformat()]["isOffDay"])
                day += manage.timedelta(days=1)
        self.assertNotIn("2026-10-08", lookup)

    def test_all_published_years_and_calendar_year_totals(self):
        expected = {2016:(30,6),2017:(28,5),2018:(29,7),2019:(28,6),2020:(30,6),
                    2021:(31,7),2022:(32,7),2023:(28,7),2024:(30,8),2025:(28,5)}
        output = manage.outputs(ROOT)
        self.assertTrue({str(y) for y in expected} <= set(json.loads(output["index.json"])["years"]))
        merged = {}
        for year, counts in expected.items():
            annual = json.loads(output[f"years/{year}.json"])
            own = [d for d in annual["days"] if d["date"].startswith(str(year))]
            self.assertEqual((sum(d["isOffDay"] for d in own),sum(not d["isOffDay"] for d in own)), counts)
            for day in annual["days"]:
                if day["date"] in merged:
                    self.assertEqual(merged[day["date"]], day)
                merged[day["date"]] = day

    def test_historical_amendments_and_december_backfill(self):
        output = manage.outputs(ROOT)
        def lookup(year):
            return {d["date"]:d for d in json.loads(output[f"years/{year}.json"])["days"]}
        y19, y20, y24 = lookup(2019), lookup(2020), lookup(2024)
        self.assertTrue(y19["2019-05-04"]["isOffDay"])
        self.assertFalse(y19["2019-04-28"]["isOffDay"])
        self.assertFalse(y19["2019-05-05"]["isOffDay"])
        for day in ["2020-01-31", "2020-02-01", "2020-02-02"]:
            self.assertTrue(y20[day]["isOffDay"])
        self.assertNotIn("2020-02-03", y20)
        self.assertNotIn("2024-02-09", y24)
        self.assertTrue(lookup(2016)["2016-12-31"]["isOffDay"])
        self.assertTrue(lookup(2022)["2022-12-31"]["isOffDay"])
        self.assertTrue(lookup(2023)["2023-12-30"]["isOffDay"])
        self.assertFalse(lookup(2018)["2018-12-29"]["isOffDay"])
        self.assertTrue(lookup(2018)["2018-12-30"]["isOffDay"])
        self.assertTrue(any("2019年" in source["title"] for source in json.loads(output["years/2018.json"])["sources"]))

    def test_bad_dates_and_conflicts(self):
        for value in ["2026-02-31", "2026-2-14", "2027-02-14"]:
            p = copy.deepcopy(self.plan)
            p["workdays"][0]["date"] = value
            with self.assertRaises(ValueError):
                manage.expand(p)
        self.plan["workdays"].append({"name": "冲突", "date": "2026-01-03"})
        with self.assertRaises(ValueError):
            manage.expand(self.plan)

    def test_incomplete_or_non_official_source_rejected(self):
        for change in [lambda p: p.update(sources=[]), lambda p: p["holidays"].pop(),
                       lambda p: p["sources"][0].update(url="https://gov.cn.example.org/notice"),
                       lambda p: p["sources"][0].update(url="https://www.gov.cn@evil.example/notice")]:
            p = copy.deepcopy(self.plan)
            change(p)
            with self.assertRaises(ValueError):
                manage.expand(p)

    def test_draft_not_published_and_hash_matches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plans").mkdir()
            (root / "plans/2026.json").write_bytes(manage.encode(self.plan))
            draft = {"year": 2027, "version": "2027.1", "status": "draft", "updatedAt": "2026-10-23", "sources": [], "holidays": [], "workdays": []}
            (root / "plans/2027.json").write_bytes(manage.encode(draft))
            output = manage.outputs(root)
            index = json.loads(output["index.json"])
            self.assertNotIn("2027", index["years"])
            self.assertNotIn("years/2027.json", output)
            self.assertEqual(index["years"]["2026"]["sha256"], hashlib.sha256(output["years/2026.json"]).hexdigest())
            manage.run(root)
            manage.run(root, check=True)
            (root / "years/2026.json").write_text("{}")
            with self.assertRaises(ValueError):
                manage.run(root, check=True)


if __name__ == "__main__":
    unittest.main()
