"""
Proof helper (companion to discover_smartrecruiters_id.py): page through a
resolved SmartRecruiters companyId's postings end to end and confirm the
data is real and internally consistent before any spider is built.

Checks, per company:
  - totalFound (from the first page) == number of unique posting ids
    actually retrieved by paginating (limit=100, offset stepping)
  - zero id overlap between consecutive pages
  - prints the first few titles + the careers-page URL for a manual
    cross-check against the live board

    PYTHONPATH=src python scripts/check_smartrecruiters_pagination.py sia cliffordchance jadeglobal

Discovery/verification only - never touches the database.
"""
import sys
import time

import requests

_API = "https://api.smartrecruiters.com/v1/companies/{cid}/postings"
_UA = {"User-Agent": "HuntLoop-ATS-research/1.0 (+sponsorship-matching)"}
_PAGE = 100


def paginate(cid: str) -> None:
    r0 = requests.get(_API.format(cid=cid), headers=_UA, params={"limit": 1}, timeout=20)
    total = int(r0.json().get("totalFound", 0) or 0)
    print(f"\n=== {cid} ===")
    print(f"careers page: https://careers.smartrecruiters.com/{cid}")
    print(f"totalFound (page 0): {total}")
    if total == 0:
        print("  nothing to paginate")
        return

    seen: set[str] = set()
    page_ids: list[set[str]] = []
    offset = 0
    while offset < total:
        r = requests.get(_API.format(cid=cid), headers=_UA,
                         params={"limit": _PAGE, "offset": offset}, timeout=20)
        if r.status_code != 200:
            print(f"  offset {offset}: HTTP {r.status_code} - stopping")
            break
        content = r.json().get("content") or []
        ids = {p["id"] for p in content}
        overlap = ids & seen
        print(f"  offset {offset:4d}: {len(content):3d} postings, "
              f"{len(overlap)} overlap with earlier pages")
        page_ids.append(ids)
        seen |= ids
        if not content:
            break
        offset += _PAGE
        time.sleep(0.3)

    consecutive_overlap = sum(
        len(page_ids[i] & page_ids[i + 1]) for i in range(len(page_ids) - 1)
    )
    print(f"  unique postings retrieved: {len(seen)}   totalFound: {total}   "
          f"match: {len(seen) == total}")
    print(f"  total cross-page id overlap: {len(seen) != sum(len(p) for p in page_ids)} "
          f"(consecutive-page overlap count: {consecutive_overlap})")

    first = requests.get(_API.format(cid=cid), headers=_UA,
                         params={"limit": 3}, timeout=20).json().get("content") or []
    print("  first 3 postings (for live cross-check):")
    for p in first:
        loc = p.get("location") or {}
        print(f"    - {p.get('name')!r}  [{loc.get('city')}, {loc.get('country')}]  "
              f"released {p.get('releasedDate', '?')[:10]}")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: check_smartrecruiters_pagination.py <companyId> [<companyId> ...]")
    for cid in sys.argv[1:]:
        paginate(cid)


if __name__ == "__main__":
    main()
