import json
import time
from pathlib import Path
import requests

HARDCOVER_API = "https://api.hardcover.app/v1/graphql"

QUERY = """
query {
  me {
    username
    name
    image { url }
    goals(limit: 1) { goal progress }

    user_books {
      status_id
      rating
      last_read_date
      book {
        title
        slug
        pages
        image { url }
        contributions { author { name } }
      }
      user_book_reads {
        progress
        started_at
        finished_at
      }
    }
  }
}
"""


def _graphql(token: str) -> dict:
    r = requests.post(
        HARDCOVER_API,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"query": QUERY},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


def fetch_hardcover_data(
    token: str,
    cache_path: Path,
    ttl_seconds: int,
    nocache: bool = False,
) -> dict:
    """
    Unified Hardcover fetch with optional cache.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if not nocache and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < ttl_seconds:
            return json.loads(cache_path.read_text())

    data = _graphql(token)
    cache_path.write_text(json.dumps(data, indent=2))
    return data
