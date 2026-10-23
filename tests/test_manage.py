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
