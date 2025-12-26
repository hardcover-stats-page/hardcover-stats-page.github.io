#!/usr/bin/env python3
import os
import json
import shutil
from pathlib import Path
from datetime import datetime, timezone
from jinja2 import Environment, FileSystemLoader

from hardcover_client import fetch_hardcover_data


ROOT = Path(__file__).parent.resolve()
DOCS_DIR = ROOT / "docs"
READING_DIR = DOCS_DIR / "reading"
STATIC_SRC = ROOT / "static"
STATIC_DST = DOCS_DIR / "static"
TEMPLATES_DIR = ROOT / "templates"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _copy_static() -> None:
    STATIC_DST.mkdir(parents=True, exist_ok=True)
    # replace completely to avoid stale files
    if STATIC_DST.exists():
        shutil.rmtree(STATIC_DST)
    shutil.copytree(STATIC_SRC, STATIC_DST)


def _compute_totals_from_finished(finished: list[dict]) -> dict:
    """
    finished: list of finished books in your normalized structure, each element e.g.
      {
        "title": "...",
        "pages": 394 or None,
        ...
      }
    """
    total_books = len(finished)
    total_pages = 0
    missing_pages = 0

    for b in finished:
        pages = b.get("pages")
        if isinstance(pages, int) and pages > 0:
            total_pages += pages
        else:
            missing_pages += 1

    return {
        "books": total_books,
        "pages": total_pages,
        "missing_pages": missing_pages,
    }


def main() -> None:
    token = os.getenv("HARDCOVER_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("HARDCOVER_API_TOKEN is missing")

    # In CI: always nocache, locally you can change it if you want
    nocache = os.getenv("NOCACHE", "1").strip() == "1"

    data = fetch_hardcover_data(token, nocache=nocache)

    # expected keys from hardcover_client.py
    me = data.get("me") or {}
    currently = data.get("currently") or []
    finished = data.get("finished") or []
    timeline = data.get("timeline") or []
    books_per_year = data.get("books_per_year") or []
    books_per_year_max = int(data.get("books_per_year_max") or 0)
    stats = data.get("stats") or {}

    # ✅ FIX: totals are required by reading.html
    totals = _compute_totals_from_finished(finished)

    build_stamp = _utc_stamp()

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    tpl = env.get_template("reading.html")

    html = tpl.render(
        me=me,
        stats=stats,
        totals=totals,  # ✅ THIS FIXES YOUR ERROR
        currently=currently,
        timeline=timeline,
        books_per_year=books_per_year,
        books_per_year_max=books_per_year_max,
        build={"stamp": build_stamp},
    )

    # output
    READING_DIR.mkdir(parents=True, exist_ok=True)
    _write_text(READING_DIR / "index.html", html)

    _write_text(
        DOCS_DIR / "build.json",
        json.dumps(
            {
                "build": build_stamp,
                "nocache": nocache,
                "counts": {
                    "currently": len(currently),
                    "finished": len(finished),
                    "timeline_years": len(timeline),
                },
                "totals": totals,
            },
            indent=2,
        ),
    )

    _copy_static()
    print(f"OK: wrote {READING_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
