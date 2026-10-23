#!/usr/bin/env python3
"""Offline-only generation and validation. Standard library, Python >= 3.9."""
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"重复 JSON 字段：{key}")
            obj[key] = value
        return obj
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"日期必须为 YYYY-MM-DD：{value!r}")
    return date.fromisoformat(value)


def name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 60:
        raise ValueError("名称必须为 1—60 个字符")
    return value


def expand(plan):
    year = plan["year"]
    if type(year) is not int or not 2007 <= year <= 2100:
        raise ValueError("年份必须在 2007—2100")
    if not re.fullmatch(rf"{year}\.[1-9]\d*", plan["version"]):
        raise ValueError("版本格式应为 年份.修订号，例如 2026.1")
    if plan["status"] not in ("draft", "published"):
        raise ValueError("status 必须为 draft 或 published")
    parse_date(plan["updatedAt"])
    if not isinstance(plan["sources"], list):
        raise ValueError("sources 必须为数组")
    for source in plan["sources"]:
        name(source["title"])
        name(source["documentNumber"])
        parse_date(source["publishedAt"])
        url = urlsplit(source["url"])
        host = url.hostname or ""
        if url.scheme != "https" or not host.endswith(".gov.cn") or url.username or url.password:
            raise ValueError("来源必须为 HTTPS 政府网站公告链接")
    days = {}

    def add(value, label, off):
        d = parse_date(value)
        # An announcement may contain the previous December's spillover dates.
        if d.year != year and not (d.year == year - 1 and d.month == 12):
            raise ValueError(f"日期超出年度范围：{value}")
        if value in days:
            raise ValueError(f"日期重复或放假与补班冲突：{value}")
        if not off and d.weekday() < 5:
            raise ValueError(f"补班日期应为周末，请人工核对：{value}")
        days[value] = {"date": value, "name": name(label), "isOffDay": off}

    labels = set()
    for period in plan["holidays"]:
        start, end = parse_date(period["start"]), parse_date(period["end"])
        if not 0 <= (end - start).days <= 30:
            raise ValueError("假期区间应为 1—31 天")
        labels.add(name(period["name"]))
        for offset in range((end - start).days + 1):
            add((start + timedelta(days=offset)).isoformat(), period["name"], True)
    for workday in plan["workdays"]:
        add(workday["date"], workday["name"], False)
    if plan["status"] == "published":
        if not plan["sources"]:
            raise ValueError("发布数据必须注明官方来源")
        if not all(any(key in label for label in labels) for key in ["元旦", "春节", "清明", "劳动", "端午", "中秋", "国庆"]):
            raise ValueError("发布数据必须覆盖全年七类假期，请核对公告是否完整")
    return {
        "schemaVersion": 1, "year": year, "version": plan["version"],
        "status": plan["status"], "timeZone": "Asia/Shanghai", "updatedAt": plan["updatedAt"],
        "sources": plan["sources"], "days": [days[key] for key in sorted(days)],
        **({"notes": plan["notes"]} if plan.get("notes") else {}),
    }


def outputs(root=ROOT):
    result, years, updates = {}, {}, []
    published = {}
    for path in sorted((root / "plans").glob("*.json")):
        plan = read(path)
        data = expand(plan)
        year = str(data["year"])
        if path.stem != year:
            raise ValueError(f"文件名和年份不同：{path.name}")
        if data["status"] != "published":
            continue  # Draft years never appear in the public index or annual endpoints.
        published[data["year"]] = data
    for year_number, original in sorted(published.items()):
        year = str(year_number)
        data = dict(original)
        days = {d["date"]: d for d in original["days"]}
        following = published.get(year_number + 1)
        if following:
            spillover = [d for d in following["days"] if d["date"].startswith(f"{year}-12-")]
            for day in spillover:
                if day["date"] in days and days[day["date"]] != day:
                    raise ValueError(f"相邻年度数据冲突：{day['date']}")
                days[day["date"]] = day
            if spillover:
                sources = {s["url"]: s for s in original["sources"]}
                sources.update({s["url"]: s for s in following["sources"]})
                data["sources"] = list(sources.values())
        data["days"] = [days[key] for key in sorted(days)]
        content = encode(data)
        result[f"years/{year}.json"] = content
        years[year] = {
            "version": data["version"], "path": f"years/{year}.json",
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        updates.append(data["updatedAt"])
    result["index.json"] = encode({
        "schemaVersion": 1, "region": "CN", "timeZone": "Asia/Shanghai",
        "updatedAt": max(updates, default="1970-01-01"), "years": years,
    })
    return result


def run(root=ROOT, check=False):
    expected = outputs(root)
    for relative, content in expected.items():
        path = root / relative
        if check:
            if not path.exists() or path.read_bytes() != content:
                raise ValueError(f"生成文件未同步：{relative}；请运行 python3 scripts/manage.py build")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    unexpected = {p.relative_to(root).as_posix() for p in (root / "years").glob("*.json")} - set(expected)
    if unexpected:
        raise ValueError(f"存在未在索引中发布的年度文件，请人工处理：{sorted(unexpected)}")
    print("校验通过" if check else "已生成年度文件和 SHA-256 索引")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "check", "new"])
    parser.add_argument("year", type=int, nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "new":
            if args.year is None or not 2007 <= args.year <= 2100:
                raise ValueError("用法：python3 scripts/manage.py new 2027")
            path = ROOT / "plans" / f"{args.year}.json"
            template = {"year": args.year, "version": f"{args.year}.1", "status": "draft", "updatedAt": date.today().isoformat(), "sources": [], "holidays": [], "workdays": []}
            with path.open("xb") as file:
                file.write(encode(template))
            print(f"已创建 {path.name} 草稿；未加入发布索引")
        else:
            run(check=args.command == "check")
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
