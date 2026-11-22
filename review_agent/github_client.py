"""Small GitHub REST client built on requests.

Only two operations are needed: fetch a pull request's diff, and post a
comment on the pull request. Keeping this on plain ``requests`` avoids a
heavy SDK dependency for two endpoints.
"""

from __future__ import annotations

import os
import re

import requests

API_BASE = "https://api.github.com"
_PR_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract (owner, repo, number) from a pull request URL."""
    match = _PR_URL_RE.search(url)
    if not match:
        raise ValueError(f"Not a GitHub pull request URL: {url}")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def _headers(token: str | None, accept: str) -> dict[str, str]:
    headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = token or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_pr_diff(pr_url: str, token: str | None = None) -> str:
    """Download the unified diff for a pull request."""
    owner, repo, number = parse_pr_url(pr_url)
    response = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/pulls/{number}",
        headers=_headers(token, "application/vnd.github.diff"),
        timeout=30,
    )
    response.raise_for_status()
    return response.text


def post_pr_comment(pr_url: str, body: str, token: str | None = None) -> str:
    """Post a comment on a pull request and return the comment URL."""
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError(
            "Posting a comment needs a GitHub token: set GITHUB_TOKEN "
            "or pass --token. The token needs pull-requests write access.")
    owner, repo, number = parse_pr_url(pr_url)
    response = requests.post(
        f"{API_BASE}/repos/{owner}/{repo}/issues/{number}/comments",
        headers=_headers(token, "application/vnd.github+json"),
        json={"body": body},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("html_url", "")
