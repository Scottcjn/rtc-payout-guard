#!/usr/bin/env python3
"""Take a stars/forks/followers snapshot for the Elyan Labs accounts.

Run it on a schedule. The first run is a baseline and reports nothing; every
run after that reports who removed a star, deleted a fork, or unfollowed.

    python3 scripts/social_snapshot.py --db ~/.elyan/social_watch.db
    python3 scripts/social_snapshot.py --db ... --report      # removals only

A fetch failure for one target is recorded as skipped, never as an empty
membership, because "the API failed" and "everyone unstarred" must not look
the same in the data.
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from rtc_payout_guard import social_watch as sw  # noqa: E402

TOKEN_FILE = os.path.expanduser("~/git.txt")
ACCOUNTS = ["Scottcjn", "sophiaeagent-beep"]

# Repos where a star, fork or follow has been paid for, so a removal is a
# reversal of something we bought. Others can be added freely.
BOUNTY_REPOS = [
    "Scottcjn/Rustchain",
    "Scottcjn/rustchain-bounties",
    "Scottcjn/bottube",
    "Scottcjn/beacon-skill",
    "Scottcjn/grazer-skill",
    "Scottcjn/ram-coffers",
    "Scottcjn/rustchain-mcp",
    "Scottcjn/clawrtc-rs",
    "sophiaeagent-beep/clawrtc",
    "sophiaeagent-beep/computational-antiquity",
]


def token() -> str:
    with open(TOKEN_FILE) as fh:
        return fh.readline().strip()


def _repo_exists(target: str, tok: str) -> bool:
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "25",
         "-H", f"Authorization: token {tok}", f"https://api.github.com/repos/{target}"],
        capture_output=True, text=True).stdout.strip()
    return r == "200"


def _get(url: str, tok: str, target_hint: tuple = ("",)) -> list:
    """Paginate a GitHub list endpoint. Raises on failure, never returns []."""
    out, page = [], 1
    while page <= 20:
        data = None
        for attempt in range(3):
            r = subprocess.run(
                ["curl", "-s", "--max-time", "60", "-H", f"Authorization: token {tok}",
                 "-H", "Accept: application/vnd.github+json",
                 f"{url}{'&' if '?' in url else '?'}per_page=100&page={page}"],
                capture_output=True, text=True,
            ).stdout
            if not r.strip():
                time.sleep(2 + attempt * 3)
                continue
            try:
                data = json.loads(r)
                break
            except json.JSONDecodeError:
                # A truncated body is a transport failure, not data. Retrying is
                # right; accepting the partial list would silently record every
                # member past the cut as removed.
                time.sleep(2 + attempt * 3)
        if data is None:
            raise RuntimeError("no parseable response after 3 attempts")
        if isinstance(data, dict):
            # GitHub 404s some list endpoints on a repo that simply has no
            # members yet. That is genuinely "zero", not a failure, but only
            # when the repo itself resolves. Getting this wrong in either
            # direction is costly: a real error recorded as zero would look
            # like every member removed at once.
            # Only page 1 with nothing collected can legitimately mean "zero
            # members". Firing this mid-scan would discard everything already
            # gathered and report the whole membership as removed.
            if (data.get("message") == "Not Found" and page == 1 and not out
                    and _repo_exists(target_hint[0], tok)):
                return []
            raise RuntimeError(data.get("message", "unexpected object"))
        if not data:
            break
        out.extend(data)
        if len(data) < 100:
            break
        page += 1
    else:
        # Loop exhausted the page cap rather than reaching the end. The list is
        # truncated, and a truncated list recorded as complete marks everyone
        # past the cut as removed.
        raise RuntimeError(f"page cap reached for {url}; list is truncated")
    return out


def make_fetch(tok: str):
    def fetch(kind: str, target: str) -> list[str]:
        if kind == sw.STAR:
            return [(u["login"], u.get("id")) for u in _get(f"https://api.github.com/repos/{target}/stargazers", tok, (target,))]
        if kind == sw.FORK:
            return [(f["owner"]["login"], f["owner"].get("id")) for f in _get(f"https://api.github.com/repos/{target}/forks?sort=oldest", tok, (target,))]
        if kind == sw.FOLLOW:
            return [(u["login"], u.get("id")) for u in _get(f"https://api.github.com/users/{target}/followers", tok)]
        raise ValueError(kind)
    return fetch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/.elyan/social_watch.db"))
    ap.add_argument("--report", action="store_true", help="print removals and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.db), exist_ok=True)
    conn = sw.connect(args.db)

    if args.report:
        rem = sw.removals(conn)
        if args.json:
            print(json.dumps(rem, indent=2))
        else:
            if not rem:
                print("no removals recorded")
            for r in rem:
                held = r["held_seconds"] / 86400.0
                print(f"  {r['kind']:6} {r['actor']:24} {r['target']:36} "
                      f"held {held:.1f}d, removed {time.strftime('%Y-%m-%d', time.gmtime(r['removed_at']))}"
                      + (f", re-added {r['reappeared']}x" if r["reappeared"] else ""))
        return 1 if rem else 0

    targets = [(sw.STAR, r) for r in BOUNTY_REPOS]
    targets += [(sw.FORK, r) for r in BOUNTY_REPOS]
    targets += [(sw.FOLLOW, a) for a in ACCOUNTS]

    results = sw.collect(conn, targets, make_fetch(token()))

    baseline = sum(1 for r in results if r.get("baseline"))
    skipped = [r for r in results if r.get("skipped")]
    added = sum(len(r.get("added", [])) for r in results)
    removed = [(r["kind"], r["target"], a) for r in results for a in r.get("removed", [])]
    reappeared = [(r["kind"], r["target"], a) for r in results for a in r.get("reappeared", [])]

    print(f"targets: {len(targets)}  baseline: {baseline}  skipped: {len(skipped)}")
    print(f"added: {added}  removed: {len(removed)}  reappeared: {len(reappeared)}")
    for k, t, a in removed:
        print(f"  REMOVED  {k:6} {a:24} {t}")
    for k, t, a in reappeared:
        print(f"  RE-ADDED {k:6} {a:24} {t}")
    for r in skipped:
        print(f"  SKIPPED  {r['kind']:6} {r['target']} -> {r['error']}")
    if skipped:
        print('  NOTE: skipped targets leave a blind spot for this run')
    return 1 if (removed or skipped) else 0


if __name__ == "__main__":
    sys.exit(main())
