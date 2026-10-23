from datetime import date
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import manage
import sync_official as sync

HTML = (ROOT / "tests/fixtures/notice-2026.html").read_bytes()
ROW = {"title":"国务院办公厅关于2026年部分节假日安排的通知", "pcode":"国办发明电〔2025〕7号", "pubtimeStr":"2025.11.04", "url":"https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm"}


class SyncChecks(unittest.TestCase):
    def test_real_official_notice_matches_manually_verified_dates(self):
        metadata = {
            2023:("2022.12.08","国办发明电〔2022〕16号"),
            2024:("2023.10.25","国办发明电〔2023〕7号"),
            2025:("2024.11.12","国办发明电〔2024〕12号"),
            2026:("2025.11.04","国办发明电〔2025〕7号"),
        }
        for year,(publication,number) in metadata.items():
            row = dict(ROW,title=f"国务院办公厅关于{year}年部分节假日安排的通知",pubtimeStr=publication,pcode=number)
            html = (ROOT / f"tests/fixtures/notice-{year}.html").read_bytes()
            with self.subTest(year=year):
                plan, _ = sync.parse_annual(row,html,date(2026,10,23))
                self.assertEqual(sync.shape(plan),sync.shape(manage.read(ROOT / f"plans/{year}.json")))

    def test_ambiguous_and_malformed_notices_are_rejected(self):
        for before, after in [("共9天", "共8天"), ("2月15日", "2月31日"),
                              ("一、元旦:", "一、未知节日:"), ("7号", "8号"),
                              ("1月4日(周日)上班", "1月5日(周一)上班")]:
            text = HTML.decode()
            self.assertIn(before, text)
            changed = text.replace(before, after)
            with self.assertRaises(ValueError):
                sync.parse_annual(ROW, changed.encode(), date(2026,10,23))

    def temp_root(self, temp):
        root = Path(temp)
        (root / "plans").mkdir()
        shutil.copy(ROOT / "plans/2025.json", root / "plans/2025.json")
        manage.run(root)
        return root

    def test_new_year_is_published_once_and_no_change_is_quiet(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.temp_root(temp)
            report = sync.process([ROW,ROW], root, fetch=lambda _:HTML, today=date(2025,11,4))
            self.assertEqual(report["published"], [2026])
            manage.run(root,check=True)
            state = (root / "automation/state.json").read_bytes()
            annual = (root / "years/2026.json").read_bytes()
            again = sync.process([ROW], root, fetch=lambda _:HTML, today=date(2025,11,5))
            self.assertEqual(again["published"], [])
            self.assertEqual(state, (root / "automation/state.json").read_bytes())
            self.assertEqual(annual, (root / "years/2026.json").read_bytes())
            sync.process([ROW], root, fetch=lambda _:HTML, today=date(2025,12,1))
            self.assertNotEqual(state, (root / "automation/state.json").read_bytes())
            self.assertEqual(annual, (root / "years/2026.json").read_bytes())

    def test_existing_dates_are_never_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.temp_root(temp)
            shutil.copy(ROOT / "plans/2026.json", root / "plans/2026.json")
            manage.run(root)
            original = (root / "years/2026.json").read_bytes()
            changed = HTML.replace("2月15日".encode(), "2月14日".encode())
            report = sync.process([ROW], root, fetch=lambda _:changed, today=date(2026,10,23))
            self.assertEqual(len(report["review"]),1)
            self.assertEqual(original, (root / "years/2026.json").read_bytes())
            reviews = list((root / "automation/reviews").glob("*.json"))
            sync.process([ROW], root, fetch=lambda _:changed, today=date(2026,10,24))
            self.assertEqual(reviews, list((root / "automation/reviews").glob("*.json")))

    def test_amendment_blocks_initial_annual_autopublish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.temp_root(temp)
            amendment = dict(ROW, title="国务院办公厅关于延长2026年春节假期的通知",pcode="国办发明电〔2026〕1号",url="https://www.gov.cn/amendment.htm")
            report = sync.process([ROW,amendment], root, fetch=lambda _:HTML,today=date(2026,1,1))
            self.assertEqual(report["published"],[])
            self.assertFalse((root / "years/2026.json").exists())
            self.assertEqual(len(report["review"]),2)

    def test_network_empty_result_and_untrusted_sources_do_not_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.temp_root(temp)
            for rows in [[], [dict(ROW,url="https://evil.example/notice")]]:
                with self.assertRaises(ValueError):
                    sync.process(rows,root,fetch=lambda _:HTML,today=date(2025,11,4))
                self.assertFalse((root / "years/2026.json").exists())
            with self.assertRaises(OSError):
                sync.process([ROW],root,fetch=lambda _:(_ for _ in ()).throw(OSError("offline")),today=date(2025,11,4))

    def test_missing_body_creates_review_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = self.temp_root(temp)
            report = sync.process([ROW],root,fetch=lambda _:b"<html>unavailable</html>",today=date(2025,11,4))
            self.assertEqual(len(report["review"]),1)
            self.assertFalse((root / "years/2026.json").exists())
            existing = list((root / "automation/reviews").glob("*.json"))[0].read_bytes()
            sync.process([ROW],root,fetch=lambda _:b"<html>unavailable</html>",today=date(2025,11,5))
            self.assertEqual(existing,list((root / "automation/reviews").glob("*.json"))[0].read_bytes())

    def test_new_year_december_spillover_revises_previous_version(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "plans").mkdir()
            shutil.copy(ROOT / "plans/2022.json",root / "plans/2022.json")
            manage.run(root)
            row = dict(ROW,title="国务院办公厅关于2023年部分节假日安排的通知",pubtimeStr="2022.12.08",pcode="国办发明电〔2022〕16号")
            html = (ROOT / "tests/fixtures/notice-2023.html").read_bytes()
            report = sync.process([row],root,fetch=lambda _:html,today=date(2022,12,8))
            self.assertEqual(report["published"],[2023])
            manage.run(root,check=True)
            self.assertEqual(manage.read(root / "plans/2022.json")["version"],"2022.2")
            days = {d["date"]:d for d in manage.read(root / "years/2022.json")["days"]}
            self.assertTrue(days["2022-12-31"]["isOffDay"])


if __name__ == "__main__":
    unittest.main()
