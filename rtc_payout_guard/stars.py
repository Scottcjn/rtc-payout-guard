"""Star-bounty claim verification against actual GitHub state.

Never trust claim text. GitHub stars cannot be private, so an empty
starred list from the API is meaningful evidence: the handle has starred
nothing visible, full stop.

Known limitation, stated honestly: this check cannot distinguish
never-starred from starred-then-unstarred. A handle that claimed a star
bounty historically and shows zero stars today either fabricated the
claim or un-starred after payment; deciding which requires payment-time
records, not this tool.
"""

from __future__ import annotations

from typing import Callable, Iterable, List

FetchStarred = Callable[[str], Iterable[str]]


def verify_star_claim(
    handle: str,
    required_repos: Iterable[str],
    fetch_starred: FetchStarred,
) -> dict:
    """Check whether ``handle`` currently stars every repo in ``required_repos``.

    Args:
        handle: GitHub login being verified.
        required_repos: iterable of ``owner/repo`` full names the bounty
            requires.
        fetch_starred: injected callable ``handle -> iterable of owner/repo``
            returning what the handle actually stars right now. Injected so
            tests run offline; use :func:`github_fetch_starred` for live runs.

    Returns:
        ``{"handle", "verified", "starred", "missing"}`` where ``starred``
        is the required repos actually starred and ``missing`` the required
        repos not starred, both preserving input order. Comparison is
        case-insensitive (GitHub repo names are).
    """
    actual = {r.strip().lower() for r in fetch_starred(handle)}
    starred: List[str] = []
    missing: List[str] = []
    for repo in required_repos:
        (starred if repo.strip().lower() in actual else missing).append(repo)
    return {
        "handle": handle,
        "verified": not missing,
        "starred": starred,
        "missing": missing,
    }


def github_fetch_starred(handle: str, session=None, per_page: int = 100) -> List[str]:
    """Fetch a user's starred repos from the public GitHub API, paginated.

    Live network helper for the CLI; everything else in this package takes
    an injected fetcher instead. Raises ``requests.HTTPError`` on API
    errors (including rate limits) rather than guessing.
    """
    import requests

    sess = session or requests.Session()
    repos: List[str] = []
    page = 1
    while True:
        resp = sess.get(
            f"https://api.github.com/users/{handle}/starred",
            params={"per_page": per_page, "page": page},
            headers={"Accept": "application/vnd.github+json"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        repos.extend(item["full_name"] for item in batch)
        if len(batch) < per_page:
            return repos
        page += 1
