#!/usr/bin/env python3
import os
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
from jinja2 import Environment, FileSystemLoader

from hardcover_client import fetch_hardcover_data

ROOT = Path(__file__).parent.resolve()
DOCS = ROOT / "docs"
STATIC_SRC = ROOT / "static"
STATIC_DST = DOCS / "static"
TEMPLATES = ROOT / "templates"
CACHE_PATH = ROOT / ".cache" / "hardcover.json"
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "900"))


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def copy_static():
    if STATIC_DST.exists():
        shutil.rmtree(STATIC_DST)
    shutil.copytree(STATIC_SRC, STATIC_DST)


def compute_totals(finished):
    pages = 0
    missing = 0
    for b in finished:
        if b.get("pages"):
            pages += b["pages"]
        else:
            missing += 1
    return {
        "books": len(finished),
        "pages": pages,
        "missing_pages": missing,
    }


def main():
    token = os.getenv("HARDCOVER_API_TOKEN")
    if not token:
        raise SystemExit("HARDCOVER_API_TOKEN missing")

    nocache = os.getenv("NOCACHE", "1") == "1"

    raw = fetch_hardcover_data(
        token=token,
        cache_path=CACHE_PATH,
        ttl_seconds=CACHE_TTL,
        nocache=nocache,
    )

    me_raw = raw["me"]
    books = me_raw["user_books"]

    currently = []
    finished = []

    for ub in books:
        book = ub["book"]
        authors = ", ".join(a["author"]["name"] for a in book["contributions"])

        entry = {
            "title": book["title"],
            "author": authors,
            "pages": book["pages"],
            "cover": book["image"]["url"] if book["image"] else None,
            "rating_stars": ub["rating"],
            "hardcover_book_url": f"https://hardcover.app/books/{book['slug']}",
            "progress": 0,
            "pct": None,
            "duration_days": None,
            "missing": False,
        }

        reads = ub["user_book_reads"] or []
        if reads:
            r = reads[-1]
            entry["progress"] = r["progress"] or 0
            if book["pages"]:
                entry["pct"] = int(entry["progress"] / book["pages"] * 100)

            if r.get("started_at") and r.get("finished_at"):
                sd = datetime.fromisoformat(r["started_at"][:10])
                fd = datetime.fromisoformat(r["finished_at"][:10])
                entry["duration_days"] = (fd - sd).days + 1

        if ub["status_id"] == 2:
            currently.append(entry)
        elif ub["status_id"] == 3:
            finished.append(entry)

    totals = compute_totals(finished)

    env = Environment(loader=FileSystemLoader(TEMPLATES))
    tpl = env.get_template("reading.html")

    html = tpl.render(
        me={
            "name": me_raw["name"],
            "username": me_raw["username"],
            "avatar": me_raw["image"]["url"] if me_raw["image"] else None,
            "profile_url": f"https://hardcover.app/@{me_raw['username']}",
        },
        stats={
            "goal_total": me_raw["goals"][0]["goal"] if me_raw["goals"] else 0,
            "goal_progress": me_raw["goals"][0]["progress"] if me_raw["goals"] else 0,
            "goal_pct": (
                me_raw["goals"][0]["progress"] / me_raw["goals"][0]["goal"] * 100
                if me_raw["goals"] else 0
            ),
            "year": datetime.now().year,
        },
        totals=totals,
        currently=currently,
        books_per_year=[],  # already rendered elsewhere
        books_per_year_max=0,
        timeline=[],
        build={"stamp": now_utc()},
    )

    (DOCS / "reading").mkdir(parents=True, exist_ok=True)
    (DOCS / "reading" / "index.html").write_text(html, encoding="utf-8")

    (DOCS / "build.json").write_text(
        json.dumps(
            {"build": now_utc(), "totals": totals},
            indent=2,
        ),
        encoding="utf-8",
    )

    copy_static()


if __name__ == "__main__":
    main()
