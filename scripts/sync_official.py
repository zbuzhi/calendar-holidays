#!/usr/bin/env python3
"""Discover official notices; publish only complete new annual plans, retain uncertain changes for review."""
import argparse
from datetime import date, datetime, timedelta
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import manage

ROOT = Path(__file__).resolve().parents[1]
SEARCH = "https://sousuo.www.gov.cn/search-gov/data"
ANNUAL = re.compile(r"^国务院办公厅关于(20\d{2})年部分节假日安排的通知$")
DAY = re.compile(r"(?:(20\d{2})年)?(?:(\d{1,2})月)?(\d{1,2})日")
HOLIDAYS = {"元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"}


class ReviewRequired(ValueError):
    pass


def clean(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", unescape(re.sub(r"<[^>]+>", "", value))))


def allowed_url(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in {"www.gov.cn", "sousuo.www.gov.cn", "app.www.gov.cn"} and not parts.username and not parts.password


def download(url):
    if not allowed_url(url):
        raise ValueError("拒绝非预设官方来源地址")
    request = Request(url, headers={"User-Agent": "calendar-holidays/1.0 (+https://github.com/zbuzhi/calendar-holidays)", "Accept": "application/json,text/html"})
    with urlopen(request, timeout=20) as response:
        if not allowed_url(response.url):
            raise ValueError("重定向超出预设官方来源")
        content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError("官方响应超过大小限制")
        return content


def search(query, fetch=download):
    params = {"t": "zhengcelibrary", "q": query, "searchfield": "title", "sort": "pubtime", "sortType": 1, "p": 0, "n": 20, "type": "gwyzcwjk", "childtype": "gongwen"}
    data = json.loads(fetch(SEARCH + "?" + urlencode(params)))
    if data.get("code") != 200:
        raise ValueError("官方检索未成功返回")
    category = data["searchVO"]["catMap"]["gongwen"]
    rows = category.get("listVO")
    if not isinstance(rows, list):
        raise ValueError("官方检索结构改变或结果缺失")
    return rows


class BodyParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.ignore, self.fragments = 0, 0, []

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "hr", "img", "input", "meta", "link", "wbr", "source"}:
            return
        if self.depth:
            self.depth += 1
            if tag in {"script", "style"}:
                self.ignore += 1
        elif dict(attrs).get("id") == "UCAP-CONTENT":
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth:
            if tag in {"script", "style"} and self.ignore:
                self.ignore -= 1
            self.depth -= 1
            if tag in {"p", "div"}:
                self.fragments.append("\n")

    def handle_data(self, value):
        if self.depth and not self.ignore:
            self.fragments.append(value)


def notice_text(html):
    parser = BodyParser()
    parser.feed(html.decode("utf-8-sig"))
    body = clean("".join(parser.fragments))
    if not body or "国务院办公厅" not in body:
        raise ReviewRequired("未找到官方公告正文，可能页面结构已改变")
    return body


def dates(text, year, label):
    result, month, previous = [], None, None
    for match in DAY.finditer(text):
        explicit_year, explicit_month, day = match.groups()
        if explicit_month:
            month = int(explicit_month)
        if month is None:
            raise ReviewRequired("日期缺少月份")
        if explicit_year:
            actual_year = int(explicit_year)
        elif previous:
            actual_year = previous.year + (month < previous.month)
        else:
            actual_year = year - 1 if label == "元旦" and month == 12 else year
        value = date(actual_year, month, int(day))
        if value.year != year and not (value.year == year - 1 and value.month == 12):
            raise ReviewRequired("日期超出本年度及上一年十二月")
        result.append(value)
        previous = value
    return result


def parse_annual(row, html, today):
    title = clean(row["title"])
    match = ANNUAL.fullmatch(title)
    if not match:
        raise ReviewRequired("不是完整年度通知")
    year = int(match.group(1))
    body = notice_text(html)
    if title not in body:
        raise ReviewRequired("正文标题与检索结果不一致")
    number = clean(row.get("pcode", ""))
    if not re.fullmatch(r"国办发明电〔20\d{2}〕\d+号", number) or number not in body:
        raise ReviewRequired("无法核验公告文号")
    published = row["pubtimeStr"].replace(".", "-")
    publication = manage.parse_date(published)
    if publication > today or publication.year not in {year - 1, year}:
        raise ReviewRequired("发布日期与年度不一致")
    headings = list(re.finditer(r"[一二三四五六七八九十]+、([^:：。]{1,24})[:：]", body))
    if not 6 <= len(headings) <= 7:
        raise ReviewRequired("年度通知的假期分节不完整")
    periods, workdays, seen = [], [], set()
    for i, heading in enumerate(headings):
        label = heading.group(1)
        names = set(re.split("[、,，]", label))
        if not names or not names <= HOLIDAYS or seen & names:
            raise ReviewRequired("假期名称重复或存在未知节日")
        seen.update(names)
        section = body[heading.end():headings[i+1].start() if i+1 < len(headings) else len(body)]
        # Lunar and weekday parentheticals must never become Gregorian dates.
        section = re.sub(r"\([^()]*\)", "", section)
        section = re.sub(r"（[^（）]*）", "", section)
        before, separator, remainder = section.partition("放假")
        if not separator:
            raise ReviewRequired("缺少明确放假安排")
        off = dates(before, year, label)
        if len(off) == 2 and not re.search(r"至|到|—|-", before):
            raise ReviewRequired("无法确定放假日期是否为区间")
        if not 1 <= len(off) <= 2:
            raise ReviewRequired("无法确定放假起止日期")
        start, end = off[0], off[-1]
        if "与周末连休" in section:
            while (start - timedelta(days=1)).weekday() >= 5:
                start -= timedelta(days=1)
            while (end + timedelta(days=1)).weekday() >= 5:
                end += timedelta(days=1)
        if "补休" in section:
            # Old annual formats need deliberate review; the modern complete range is unambiguous.
            raise ReviewRequired("含补休表述，需人工核对")
        count = (end - start).days + 1
        if not 1 <= count <= 15:
            raise ReviewRequired("假期长度异常")
        stated = re.search(r"(?:共|放假)(\d+)天", section)
        if stated and int(stated.group(1)) != count:
            raise ReviewRequired("假期起止日期与公告天数不一致")
        periods.append({"name": label, "start": start.isoformat(), "end": end.isoformat()})
        for sentence in section.split("。"):
            if "上班" not in sentence:
                continue
            prefix, tail = sentence.split("上班", 1)
            if tail:
                raise ReviewRequired("上班条款存在未识别内容")
            found = dates(prefix, year, label)
            residue = DAY.sub("", prefix)
            residue = re.sub(r"[、,，]", "", residue)
            if residue or not found:
                raise ReviewRequired("上班条款无法完整解析")
            workdays.extend({"name": label + "调休补班", "date": d.isoformat()} for d in found)
        # Do not silently ignore additional rest-day instructions outside the standard sentence.
        if "延长" in section or "调整为" in section or section.count("放假") > 1:
            raise ReviewRequired("存在额外调整表述")
    if seen != HOLIDAYS:
        raise ReviewRequired("未覆盖七类全国假期")
    plan = {"year": year, "version": f"{year}.1", "status": "published", "updatedAt": today.isoformat(),
            "sources": [{"title": title, "documentNumber": number, "publishedAt": published, "url": row["url"]}],
            "holidays": periods, "workdays": workdays}
    manage.expand(plan)
    return plan, hashlib.sha256(body.encode()).hexdigest()


def shape(plan):
    return {d["date"]: d["isOffDay"] for d in manage.expand(plan)["days"]}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(manage.encode(value))


def revise_previous(root, year, today):
    """The following year's December spillovers revise the previous annual endpoint."""
    path = root / "plans" / f"{year-1}.json"
    if not path.exists():
        return
    prior = manage.read(path)
    if prior["status"] != "published":
        return
    previous_output = root / "years" / f"{year-1}.json"
    after = manage.outputs(root).get(f"years/{year-1}.json")
    if previous_output.exists() and after is not None:
        # Ignore metadata-only differences; bump the year only when days/source coverage changes.
        before, generated = json.loads(previous_output.read_bytes()), json.loads(after)
        if before["days"] != generated["days"] or before["sources"] != generated["sources"]:
            revision = int(prior["version"].split(".")[1]) + 1
            prior["version"] = f"{year-1}.{revision}"
            prior["updatedAt"] = today.isoformat()
            write(path, prior)


def process(rows, root=ROOT, fetch=download, today=None):
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    state_path = root / "automation" / "state.json"
    state = manage.read(state_path) if state_path.exists() else {"schemaVersion": 1, "notices": {}}
    report = {"published": [], "review": [], "checkedYears": [today.year, today.year + 1]}
    unique = {}
    for row in rows:
        title = clean(row.get("title", ""))
        year_match = re.search(r"(20\d{2})年", title)
        if not year_match or int(year_match.group(1)) not in report["checkedYears"]:
            continue
        if not title.startswith("国务院办公厅关于") or not any(k in title for k in ["节假日", "假期", "放假"]):
            continue
        url = row.get("url", "")
        if not allowed_url(url):
            raise ValueError("检索结果指向非预设官方来源")
        identity = clean(row.get("pcode", "")) or url
        unique[identity] = row
    if not unique:
        raise ValueError("未检索到本年度的官方通知，不能将空响应视作无更新")
    # Never auto-publish a new year if a separate amendment for that year also exists.
    amendment_years = {int(re.search(r"(20\d{2})年", clean(r["title"])).group(1)) for r in unique.values() if not ANNUAL.fullmatch(clean(r["title"]))}
    for identity, row in sorted(unique.items()):
        title = clean(row["title"])
        year = int(re.search(r"(20\d{2})年", title).group(1))
        prior_notice = state["notices"].get(identity, {})
        html = b""
        try:
            html = fetch(row["url"])
            body = notice_text(html)
            digest = hashlib.sha256(body.encode()).hexdigest()
            if prior_notice.get("sha256") == digest:
                continue
            if not ANNUAL.fullmatch(title):
                raise ReviewRequired("发现年度安排以外的调整或延长通知")
            try:
                candidate, digest = parse_annual(row, html, today)
            except (ValueError, KeyError, TypeError) as error:
                raise ReviewRequired(str(error)) from error
            path = root / "plans" / f"{year}.json"
            if path.exists():
                existing = manage.read(path)
                if existing["status"] != "published" or shape(existing) != shape(candidate):
                    raise ReviewRequired("已存在的年度数据与官方新正文不同，需要核对修订")
                outcome = "verified-existing"
            else:
                if year in amendment_years:
                    raise ReviewRequired("同年度另有调整通知，不能只发布初始年度安排")
                write(path, candidate)
                revise_previous(root, year, today)
                manage.run(root)
                outcome = "published"
                report["published"].append(year)
            state["notices"][identity] = {"year": year, "title": title, "url": row["url"], "sha256": digest, "outcome": outcome}
        except ReviewRequired as error:
            try:
                digest = hashlib.sha256(notice_text(html).encode()).hexdigest()
            except ReviewRequired:
                digest = hashlib.sha256(html).hexdigest()
            if prior_notice.get("sha256") == digest and prior_notice.get("outcome") == "review-required":
                continue
            review_key = hashlib.sha256((identity + digest).encode()).hexdigest()[:16]
            review = {"year": year, "title": title, "url": row["url"], "documentNumber": clean(row.get("pcode", "")), "detectedAt": today.isoformat(), "sha256": digest, "reason": str(error)}
            write(root / "automation" / "reviews" / f"{year}-{review_key}.json", review)
            state["notices"][identity] = {"year": year, "title": title, "url": row["url"], "sha256": digest, "outcome": "review-required"}
            report["review"].append(review)
    # A small monthly health commit keeps public-repository schedules active without changing calendar data.
    state["lastSuccessfulMonth"] = today.strftime("%Y-%m")
    write(state_path, state)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("/tmp/holiday-sync-report.json"))
    args = parser.parse_args()
    try:
        rows = []
        for query in ["部分节假日安排", "假期", "放假"]:
            rows.extend(search(query))
        report = process(rows)
        manage.run(check=True)
        write(args.report, report)
        print(json.dumps(report, ensure_ascii=False))
        if report["review"]:
            print("::warning::发现需要人工核对的官方通知，请查看 automation/reviews 和运行摘要")
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as summary:
                summary.write(f"## 官方节假日检测\n\n检查年份：{report['checkedYears']}\n\n自动发布：{report['published'] or '无'}\n\n新增待核对记录：{len(report['review'])}\n")
        return 0
    except Exception as error:
        print(f"官方检测失败，现有发布数据不变：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
