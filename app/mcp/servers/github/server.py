"""GitHub MCP server.

Provides repository-host tools. When GITHUB_TOKEN is set it talks to the real
GitHub REST API; otherwise it runs in OFFLINE DEMO mode and returns realistic
fixtures so the whole agent is runnable without any external accounts.

The `create_pull_request` tool is intentionally a *write* action — the agent
must route it through the human-in-the-loop approval node before calling it.

Tools:
  - list_recent_commits:  recent commits on a branch
  - get_commit_diff:      the patch for a specific commit
  - list_workflow_runs:   recent GitHub Actions CI runs (status/conclusion)
  - get_failed_job_logs:  logs of the failed job(s) in a run ("why did the deploy fail")
  - create_pull_request:  (WRITE) open a PR with a proposed fix
"""

from __future__ import annotations

import os
import re

from mcp.server.fastmcp import FastMCP

# MCP servers run as subprocesses - use env vars directly, not app.config
# (which may fail to load in the subprocess context)
_GITHUB_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT_GITHUB", "15.0"))

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# "owner/repo" the real-API calls target. Required only in real (token) mode.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()
OFFLINE = not GITHUB_TOKEN

mcp = FastMCP("github")


def _repo_or_error() -> tuple[str | None, dict | None]:
    """Return (repo, None) when configured, else (None, error-dict). Avoids the
    old hardcoded OWNER/REPO placeholders that 404'd on every real-mode call."""
    if not GITHUB_REPO or "/" not in GITHUB_REPO:
        return None, {
            "error": "GITHUB_REPO is not set (expected 'owner/repo'). "
            "Set it to use the real GitHub API, or unset GITHUB_TOKEN for the offline demo."
        }
    return GITHUB_REPO, None


def _as_int(value, default: int) -> int:
    """Coerce a tool argument to int; LLMs often send numbers as strings."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- Offline fixtures (multi-scenario support) -----------------------------
def _get_commits() -> list:
    from app.mcp.fixtures import get_fixture
    return get_fixture("github", "commits") or []


def _get_diff(sha: str) -> str:
    from app.mcp.fixtures import get_fixture
    return get_fixture("github", f"diff_{sha}") or ""


@mcp.tool()
def list_recent_commits(branch: str = "main", max_count: int | str = 5) -> list[dict]:
    """List recent commits on a branch (sha, author, date, message)."""
    max_count = _as_int(max_count, 5)
    if OFFLINE:
        return _get_commits()[:max_count]
    repo, err = _repo_or_error()
    if err:
        return [err]

    from app.mcp.http_client import http_get, MCPHTTPError

    try:
        resp = http_get(
            f"https://api.github.com/repos/{repo}/commits",
            params={"sha": branch, "per_page": max_count},
            headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
            timeout=_GITHUB_TIMEOUT,
            max_retries=3,
        )
        return [
            {
                "sha": c["sha"][:7],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"][:10],
                "message": c["commit"]["message"].splitlines()[0],
            }
            for c in resp.json()
        ]
    except MCPHTTPError as e:
        return [{"error": str(e), "retries_attempted": e.retries_attempted}]


@mcp.tool()
def get_commit_diff(sha: str) -> str:
    """Return the unified diff/patch introduced by a commit."""
    if OFFLINE:
        return _get_diff(sha) or "(no diff available for this commit in offline mode)"
    repo, err = _repo_or_error()
    if err:
        return err["error"]

    from app.mcp.http_client import http_get, MCPHTTPError

    try:
        resp = http_get(
            f"https://api.github.com/repos/{repo}/commits/{sha}",
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3.diff",
            },
            timeout=_GITHUB_TIMEOUT,
            max_retries=3,
        )
        return resp.text
    except MCPHTTPError as e:
        return f"Error fetching diff: {e}"


def _get_workflow_runs() -> list:
    from app.mcp.fixtures import get_fixture
    runs = get_fixture("github", "workflow_runs")
    if runs:
        return runs
    commits = _get_commits()
    if not commits:
        return []
    return [
        {"id": 980001, "name": "CI", "event": "push", "branch": "main",
         "status": "completed", "conclusion": "failure", "head_sha": commits[0].get("sha", ""),
         "created_at": commits[0].get("date", "") + "T09:50:00Z",
         "html_url": "https://github.com/acme/repo/actions/runs/980001",
         "title": commits[0].get("message", "")},
    ]


def _get_failed_job_log() -> str:
    from app.mcp.fixtures import get_fixture
    return get_fixture("github", "failed_job_log") or "(no failed job logs in offline mode)"


# --- "What changed" correlation -------------------------------------------- #
# ~80% of incidents trace to a recent change, so ranking recent commits by their
# textual overlap with the incident signal (failing symbol / file / keywords) is
# one of the highest-ROI moves. Scoring is a pure helper so it's unit-testable.
_STOP = {
    "add", "the", "to", "for", "of", "and", "fix", "fixes", "update", "updates",
    "support", "refactor", "bump", "in", "on", "a", "an", "with", "from", "into",
}


def _tokenize(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (text or "").lower())} - _STOP


def _overlap(signal_tokens: set[str], message: str) -> list[str]:
    """Tokens shared between the incident signal and a commit message, counting a
    containment match (e.g. 'applyDiscount' ↔ 'discount') so symbol names hit."""
    msg_tokens = _tokenize(message)
    matched: set[str] = set()
    for s in signal_tokens:
        for m in msg_tokens:
            if s == m or (len(s) >= 4 and len(m) >= 4 and (s in m or m in s)):
                matched.add(m)
    return sorted(matched)


def _rank_commits(signal: str, commits: list[dict]) -> list[dict]:
    """Score commits (newest-first) by overlap with the signal; return ranked,
    stable-sorted so recency breaks ties."""
    sig = _tokenize(signal)
    scored = []
    for i, c in enumerate(commits):
        matched = _overlap(sig, c.get("message", ""))
        scored.append({**c, "score": len(matched), "matched": matched, "_i": i})
    scored.sort(key=lambda c: (-c["score"], c["_i"]))
    for c in scored:
        c.pop("_i", None)
    return scored


def _first_bad_deploy(commits: list[dict], onset_date: str) -> dict:
    """Given commits (each with a 'date' YYYY-MM-DD) and an incident onset date,
    return the most recent commit dated on/before onset (the suspect deploy) plus
    the commits that landed after it. Pure + testable. Turns text-overlap guessing
    into a time-anchored claim: the last change before the error began."""
    onset_day = (onset_date or "")[:10]
    dated = [c for c in commits if c.get("date")]
    # Newest first; the first commit at/before onset is the last deploy before the incident.
    dated.sort(key=lambda c: c["date"], reverse=True)
    after = [c for c in dated if c["date"] > onset_day]
    suspect = next((c for c in dated if c["date"] <= onset_day), None)
    return {
        "onset_date": onset_day,
        "suspect": suspect,
        "why": (
            f"Commit {suspect['sha']} ({suspect['date']}) is the last change at or "
            f"before the incident onset — the prime suspect." if suspect else
            "No commit dated at/before the onset; the cause may predate the history window."
        ),
        "landed_after_onset": after,
    }


@mcp.tool()
def first_bad_deploy(onset_timestamp: str, branch: str = "main") -> dict:
    """Time-anchored deploy bisect: given when the incident began, identify the last
    deploy before onset (the prime suspect) — a causal claim, not a keyword guess.
    Pair with `correlate_changes` (content match) for a stronger verdict."""
    commits = list_recent_commits(branch=branch, max_count=20)
    if commits and isinstance(commits[0], dict) and commits[0].get("error"):
        return {"error": commits[0]["error"]}
    return _first_bad_deploy(commits, onset_timestamp)


@mcp.tool()
def correlate_changes(signal: str, branch: str = "main", max_results: int | str = 5) -> dict:
    """Rank recent commits by how strongly they relate to an incident signal — the
    'what changed' step. Pass the failing symbol/file/keywords (e.g.
    "applyDiscount checkout.js discount 500") and get the most suspect commits
    plus the top one's diff.

    Returns {"suspects": [{sha, date, message, score, matched}], "top_diff": "..."}.
    """
    max_results = _as_int(max_results, 5)
    commits = list_recent_commits(branch=branch, max_count=20)
    # list_recent_commits returns an error-dict list in some failure modes.
    if commits and isinstance(commits[0], dict) and commits[0].get("error"):
        return {"error": commits[0]["error"]}
    ranked = _rank_commits(signal, commits)[:max_results]
    top_diff = ""
    if ranked and ranked[0]["score"] > 0:
        top_diff = get_commit_diff(ranked[0]["sha"])
    return {"signal": signal, "suspects": ranked, "top_diff": top_diff}


@mcp.tool()
def list_workflow_runs(branch: str = "main", max_count: int | str = 10) -> list[dict]:
    """List recent GitHub Actions runs (id, name, status, conclusion, head_sha) —
    the 'did the last deploy/CI pass?' signal to correlate with an incident's onset."""
    max_count = _as_int(max_count, 10)
    if OFFLINE:
        return _get_workflow_runs()[:max_count]
    repo, err = _repo_or_error()
    if err:
        return [err]
    import httpx

    resp = httpx.get(
        f"https://api.github.com/repos/{repo}/actions/runs",
        params={"branch": branch, "per_page": max_count},
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=_GITHUB_TIMEOUT,
    )
    resp.raise_for_status()
    return [
        {
            "id": r["id"], "name": r.get("name"), "event": r.get("event"),
            "branch": r.get("head_branch"), "status": r.get("status"),
            "conclusion": r.get("conclusion"), "head_sha": (r.get("head_sha") or "")[:7],
            "created_at": r.get("created_at"), "html_url": r.get("html_url"),
        }
        for r in resp.json().get("workflow_runs", [])
    ]


@mcp.tool()
def get_failed_job_logs(run_id: int | str, max_chars: int | str = 4000) -> dict:
    """Return the log tail of the FAILED job(s) in a workflow run — answers
    'why did the deploy/CI fail' and surfaces the failing test/stack trace."""
    max_chars = _as_int(max_chars, 4000)
    if OFFLINE:
        return {
            "run_id": _as_int(run_id, 0),
            "failed_jobs": [{"name": "test", "conclusion": "failure",
                             "log_tail": _get_failed_job_log()[:max_chars]}],
        }
    repo, err = _repo_or_error()
    if err:
        return err
    import httpx

    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    jobs = httpx.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs",
        headers=headers, timeout=_GITHUB_TIMEOUT,
    )
    jobs.raise_for_status()
    failed = []
    for job in jobs.json().get("jobs", []):
        if job.get("conclusion") == "failure":
            log = httpx.get(
                f"https://api.github.com/repos/{repo}/actions/jobs/{job['id']}/logs",
                headers=headers, timeout=20, follow_redirects=True,
            )
            tail = log.text[-max_chars:] if log.status_code < 400 else f"(could not fetch logs: {log.status_code})"
            failed.append({"name": job.get("name"), "conclusion": "failure", "log_tail": tail})
    return {"run_id": _as_int(run_id, 0), "failed_jobs": failed or [{"note": "no failed jobs in this run"}]}


@mcp.tool()
def create_pull_request(
    title: str, body: str, head: str, base: str = "main", patch: str = ""
) -> dict:
    """(WRITE) Open a pull request proposing a fix.

    This mutates the remote — the agent must obtain human approval first.
    In offline mode it returns a simulated PR object.

    `patch` (optional): the fix as a unified diff (``--- a/file`` / ``+++ b/file``
    hunks) over the target repo. When provided, OmniPotent can apply it to a
    throwaway repo copy and run a reproducer to PROVE the fix resolves the incident
    (sandbox counterfactual). Always include it when you can express the fix as a
    concrete code change — a verified fix is far more trustworthy than a described one.
    """
    if OFFLINE:
        return {
            "status": "created (simulated — offline demo mode, no real PR opened)",
            "url": "https://github.com/<owner>/<repo>/pull/42  (placeholder)",
            "title": title,
            "head": head,
            "base": base,
            "has_patch": bool(patch and patch.strip()),
        }
    repo, err = _repo_or_error()
    if err:
        return err
    import httpx

    resp = httpx.post(
        f"https://api.github.com/repos/{repo}/pulls",
        json={"title": title, "body": body, "head": head, "base": base},
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}"},
        timeout=_GITHUB_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"status": "created", "url": data["html_url"], "title": title}


if __name__ == "__main__":
    mcp.run(transport="stdio")
