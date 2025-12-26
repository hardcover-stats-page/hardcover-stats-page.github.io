#!/usr/bin/env python3
"""
Build static Hardcover dashboard for GitHub Pages.

Outputs:
  - docs/reading/index.html
  - docs/static/styles.css (copied)
  - docs/build.json   (proof file: stamp + first current progress)

Required env:
  HARDCOVER_API_TOKEN

Optional env:
  CACHE_PATH (default: .cache/hardcover_cache.json)
  CACHE_TTL_SECONDS (default: 900)
  NOCACHE (default: 0)
"""

import json
import os
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from jinja2 import Environment, FileSystemLoader, select_autoescape

from hardcover_client import fetch_hardcover_data

MONTH_NAMES_DE = {
    1: "Januar",
    2: "Februar",
    3: "März",
    4: "April",
    5: "Mai",
    6: "Juni",
    7: "Juli",
    8: "August",
    9: "September",
    10: "Oktober",
    11: "November",
    12: "Dezember",
}


def _safe_int(x: Any, default: int = 0) -> int:
    try:
        if x is None:
            return default
        return int(round(float(x)))
    except Exception:
        return default


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s2 = str(s).strip()
    try:
        return datetime.fromisoformat(s2.replace("Z", "+00:00"))
    except Exception:
        pass
    try:
        return datetime.strptime(s2[:10], "%Y-%m-%d")
    except Exception:
        return None


def _days_between(start: Optional[datetime], end: Optional[datetime]) -> Optional[int]:
    if not start or not end:
        return None
    try:
        return max(0, (end.date() - start.date()).days) + 1
    except Exception:
        return None


def _rating_to_stars(rating: Any) -> Optional[int]:
    # Hardcover may return 1-5 or 0-10. Map to 1-5.
    if rating is None:
        return None
    try:
        r = float(rating)
        if r <= 0:
            return None
        if r <= 5:
            return int(round(r))
        return int(round(r / 2.0))
    except Exception:
        return None


def _author_names(contribs: List[Dict[str, Any]]) -> str:
    names = []
    for c in contribs or []:
        a = (c or {}).get("author") or {}
        if a.get("name"):
            names.append(a["name"])
    return ", ".join(names) if names else "Unknown"


def _book_url(title: Optional[str], slug: Optional[str]) -> Optional[str]:
    if slug:
        return f"https://hardcover.app/books/{slug}"
    if title:
        return f"https://hardcover.app/search?q={title}"
    return None


def _normalize_me(me_raw: Any) -> Dict[str, Any]:
    if isinstance(me_raw, dict):
        return me_raw
    if isinstance(me_raw, list):
        return me_raw[0] if me_raw else {}
    return {}


def _pick_read_record(user_book_reads: Any) -> Dict[str, Any]:
    """
    Avoid "static progress" caused by taking user_book_reads[0] (often an old record).
    Strategy:
      1) keep only dict entries
      2) pick record with highest progress
      3) if all progress are 0/None -> fallback to last element
    """
    if not isinstance(user_book_reads, list) or not user_book_reads:
        return {}
    candidates = [x for x in user_book_reads if isinstance(x, dict)]
    if not candidates:
        return {}
    candidates_sorted = sorted(candidates, key=lambda r: _safe_int(r.get("progress"), 0), reverse=True)
    best = candidates_sorted[0]
    if _safe_int(best.get("progress"), 0) > 0:
        return best
    return candidates[-1]


def _missing_badges(item: Dict[str, Any]) -> List[str]:
    missing = []
    if not item.get("pages"):
        missing.append("pages")
    if item.get("rating_stars") is None:
        missing.append("rating")
    if not item.get("started_at"):
        missing.append("started_at")
    if item.get("is_finished") and not item.get("finished_at"):
        missing.append("finished_at")
    return missing


def _build_currently(me: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ub in (me.get("currently_reading") or []):
        book = (ub or {}).get("book") or {}
        r0 = _pick_read_record((ub or {}).get("user_book_reads"))

        pages = _safe_int(book.get("pages"))
        progress = _safe_int((r0 or {}).get("progress"))

        pct = int(round((progress / pages) * 100)) if pages > 0 else None

        item = {
            "title": book.get("title"),
            "author": _author_names(book.get("contributions") or []),
            "cover": ((book.get("image") or {}).get("url")),
            "pages": pages if pages > 0 else None,
            "progress": progress,  # whole number pages
            "pct": pct,            # whole number %
            "slug": book.get("slug"),
            "hardcover_book_url": _book_url(book.get("title"), book.get("slug")),
            "rating_stars": _rating_to_stars((ub or {}).get("rating")),
            "started_at": (r0 or {}).get("started_at"),
            "finished_at": None,
            "duration_days": None,
            "is_finished": False,
        }
        item["missing"] = _missing_badges(item)
        out.append(item)
    return out


def _build_finished(recently_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    finished: List[Dict[str, Any]] = []

    for ub in recently_raw or []:
        book = (ub or {}).get("book") or {}

        title = book.get("title")
        slug = book.get("slug")
        cover = ((book.get("image") or {}).get("url"))
        pages = _safe_int(book.get("pages"))
        author = _author_names(book.get("contributions") or [])

        last_read = _parse_dt((ub or {}).get("last_read_date"))
        r0 = _pick_read_record((ub or {}).get("user_book_reads"))

        started_at = _parse_dt((r0 or {}).get("started_at"))
        finished_at = _parse_dt((r0 or {}).get("finished_at")) or last_read
        if not finished_at:
            continue

        duration_days = _days_between(started_at, finished_at)
        rating_stars = _rating_to_stars((ub or {}).get("rating"))
        f_date = finished_at.date()

        item = {
            "title": title,
            "author": author,
            "cover": cover,
            "pages": pages if pages > 0 else None,
            "slug": slug,
            "hardcover_book_url": _book_url(title, slug),
            "rating_stars": rating_stars,
            "started_at": (started_at.date().isoformat() if started_at else None),
            "finished_at": f_date.isoformat(),
            "duration_days": duration_days,
            "year": f_date.year,
            "month": f_date.month,
            "month_name": MONTH_NAMES_DE.get(f_date.month, f"Monat {f_date.month}"),
            "is_finished": True,
        }
        item["missing"] = _missing_badges(item)
        finished.append(item)

    finished.sort(key=lambda x: x.get("finished_at") or "", reverse=True)
    return finished


def _group_timeline(books: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[int, Dict[int, List[Dict[str, Any]]]] = {}
    for b in books:
        grouped.setdefault(b["year"], {}).setdefault(b["month"], []).append(b)

    timeline: List[Dict[str, Any]] = []
    for year in sorted(grouped.keys(), reverse=True):
        months: List[Dict[str, Any]] = []
        year_count = 0
        for month in sorted(grouped[year].keys(), reverse=True):
            items = grouped[year][month]
            items.sort(key=lambda x: x.get("finished_at") or "", reverse=True)
            months.append(
                {
                    "month": month,
                    "month_name": MONTH_NAMES_DE.get(month),
                    "count": len(items),
                    "books": items,
                }
            )
            year_count += len(items)
        timeline.append({"year": year, "count": year_count, "months": months})
    return timeline


def _books_per_year(books: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    counter: Dict[int, int] = {}
    for b in books:
        counter[b["year"]] = counter.get(b["year"], 0) + 1
    arr = [{"year": y, "count": c} for y, c in sorted(counter.items())]
    maxv = max([x["count"] for x in arr], default=0)
    return arr, maxv


def _avg_median_days(books: List[Dict[str, Any]]) -> Dict[str, Any]:
    days = [b["duration_days"] for b in books if b.get("duration_days") is not None]
    if not days:
        return {"avg_days": None, "median_days": None}
    return {"avg_days": sum(days) / len(days), "median_days": float(median(days))}


def _monthly_streak(books: List[Dict[str, Any]]) -> Dict[str, Any]:
    ym = sorted({(b["year"], b["month"]) for b in books})
    best = 0
    if ym:
        run = 1
        for i in range(1, len(ym)):
            y1, m1 = ym[i - 1]
            y2, m2 = ym[i]
            n1 = y1 * 12 + (m1 - 1)
            n2 = y2 * 12 + (m2 - 1)
            run = run + 1 if (n2 - n1 == 1) else 1
            best = max(best, run)
        best = max(best, run)

    now = date.today()
    ym_set = set(ym)
    current = 0
    y, m = now.year, now.month
    while (y, m) in ym_set:
        current += 1
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    return {"monthly_current": current, "monthly_best": best}


def _copy_assets_to_docs() -> None:
    src = Path("static")
    dst = Path("docs") / "static"
    if not src.exists():
        print("[WARN] ./static does not exist -> skipping asset copy")
        return
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.rglob("*"):
        if p.is_file():
            rel = p.relative_to(src)
            out = dst / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)


def main() -> None:
    token = os.getenv("HARDCOVER_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing HARDCOVER_API_TOKEN")

    cache_path = os.getenv("CACHE_PATH", ".cache/hardcover_cache.json")
    ttl = int(os.getenv("CACHE_TTL_SECONDS", "900"))
    nocache = os.getenv("NOCACHE", "0") == "1"

    data = fetch_hardcover_data(token, cache_path, ttl, nocache=nocache)
    me = _normalize_me(data.get("me"))

    build_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    currently = _build_currently(me)
    finished = _build_finished(me.get("recently_read") or [])
    timeline = _group_timeline(finished)
    books_per_year, books_per_year_max = _books_per_year(finished)

    avg_med = _avg_median_days(finished)
    streak = _monthly_streak(finished)

    goals = me.get("goals") or []
    g0 = goals[0] if isinstance(goals, list) and goals else None
    goal_total = _safe_int((g0 or {}).get("goal"))
    goal_progress = _safe_int((g0 or {}).get("progress"))
    goal_pct = (goal_progress / goal_total * 100) if goal_total > 0 else None

    env = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html"]))
    tpl = env.get_template("reading.html")

    vm = {
        "rel_root": "../",  # for docs/reading/index.html -> ../static/styles.css and ../build.json
        "build": {"stamp": build_stamp},
        "me": {
            "username": me.get("username"),
            "name": me.get("name") or me.get("username"),
            "avatar": ((me.get("image") or {}).get("url")),
            "profile_url": f"https://hardcover.app/@{me.get('username')}" if me.get("username") else None,
        },
        "currently": currently,
        "timeline": timeline,
        "finished_count": len(finished),
        "books_per_year": books_per_year,
        "books_per_year_max": books_per_year_max,
        "stats": {
            "goal_total": goal_total,
            "goal_progress": goal_progress,
            "goal_pct": goal_pct,
            "avg_days": avg_med["avg_days"],
            "median_days": avg_med["median_days"],
            "streak_monthly_current": streak["monthly_current"],
            "streak_monthly_best": streak["monthly_best"],
        },
    }

    out_html = tpl.render(**vm)

    out_dir = Path("docs") / "reading"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(out_html, encoding="utf-8")

    # Proof file to diagnose "static data" vs "stale deploy"
    build_proof = {
        "build_stamp_utc": build_stamp,
        "username": me.get("username"),
        "currently_reading_count": len(me.get("currently_reading") or []),
        "first_current_title": (currently[0]["title"] if currently else None),
        "first_current_progress": (currently[0]["progress"] if currently else None),
        "first_current_pct": (currently[0]["pct"] if currently else None),
    }
    Path("docs").mkdir(exist_ok=True)
    Path("docs/build.json").write_text(json.dumps(build_proof, ensure_ascii=False, indent=2), encoding="utf-8")

    _copy_assets_to_docs()

    print(f"[OK] wrote: {out_file.resolve()}")
    print("[OK] wrote: docs/build.json")
    print("[OK] local test: python -m http.server -d docs 8000 -> http://localhost:8000/reading/")


if __name__ == "__main__":
    main()
