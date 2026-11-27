"""End-to-end tests for the review pipeline in demo mode.

Everything here runs offline with no API key: the demo model answers the
reviewer prompts with rule-based findings, so the tests can assert on the
exact issues planted in examples/sample_pr.diff.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from review_agent.agents import SEVERITY_ORDER
from review_agent.api import app
from review_agent.graph import run_review

SAMPLE_DIFF = (Path(__file__).parent.parent / "examples" / "sample_pr.diff")


@pytest.fixture(scope="module")
def result() -> dict:
    return run_review(SAMPLE_DIFF.read_text(encoding="utf-8"), demo=True,
                      source="sample")


@pytest.fixture(scope="module")
def findings(result) -> list[dict]:
    return result["review"]["findings"]


def test_demo_mode_is_flagged(result):
    assert result["demo"] is True


def test_sql_injection_is_found(findings):
    matches = [f for f in findings if "sql injection" in f["title"].lower()]
    assert matches, "planted SQL injection was not reported"
    assert matches[0]["severity"] == "critical"
    assert matches[0]["agent"] == "security"


def test_hardcoded_secret_is_found(findings):
    matches = [f for f in findings
               if "secret" in f["title"].lower()
               or "hardcoded" in f["title"].lower()]
    assert matches, "planted hardcoded API key was not reported"
    assert matches[0]["severity"] == "critical"


def test_eval_is_flagged(findings):
    matches = [f for f in findings if "eval" in f["title"].lower()]
    assert matches, "planted eval() call was not reported"
    assert matches[0]["severity"] == "high"
    assert matches[0]["agent"] == "security"


def test_exec_is_flagged():
    diff = (
        "diff --git a/app/plugins.py b/app/plugins.py\n"
        "--- a/app/plugins.py\n"
        "+++ b/app/plugins.py\n"
        "@@ -0,0 +1,6 @@\n"
        '+"""Plugin helpers."""\n'
        "+\n"
        "+\n"
        "+def run_plugin(code):\n"
        '+    """Run plugin code."""\n'
        "+    exec(code)\n"
    )
    result = run_review(diff, demo=True)
    titles = [f["title"].lower() for f in result["review"]["findings"]]
    assert any("exec" in t for t in titles), "exec() call was not reported"


def test_none_dereference_and_bare_except_are_found(findings):
    titles = " ".join(f["title"].lower() for f in findings)
    assert "none" in titles
    assert "bare except" in titles


def test_findings_are_ranked_by_severity(findings):
    ranks = [SEVERITY_ORDER[f["severity"]] for f in findings]
    assert ranks == sorted(ranks), "findings are not in severity order"
    assert findings[0]["severity"] == "critical"


def test_verdict_is_request_changes(result):
    assert result["review"]["verdict"] == "request changes"


def test_markdown_mentions_verdict_and_top_issue(result):
    markdown = result["markdown"]
    assert "Request changes" in markdown
    assert "SQL injection" in markdown


def test_clean_diff_is_approved():
    clean = (
        "diff --git a/app/math_utils.py b/app/math_utils.py\n"
        "--- a/app/math_utils.py\n"
        "+++ b/app/math_utils.py\n"
        "@@ -1,2 +1,5 @@\n"
        " def add(a, b):\n"
        '+    """Add two numbers and return the result."""\n'
        "     return a + b\n"
    )
    result = run_review(clean, demo=True)
    assert result["review"]["verdict"] == "approve"
    assert result["review"]["findings"] == []


def test_api_review_endpoint():
    client = TestClient(app)
    response = client.post(
        "/review",
        json={"diff": SAMPLE_DIFF.read_text(encoding="utf-8"),
              "demo": True, "source": "test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["review"]["verdict"] == "request changes"
    assert payload["demo"] is True


def test_api_health_endpoint():
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}


def test_min_severity_filters_findings(findings):
    result = run_review(SAMPLE_DIFF.read_text(encoding="utf-8"), demo=True,
                        min_severity="high")
    kept = result["review"]["findings"]
    assert kept, "expected at least the critical findings to survive"
    assert all(f["severity"] in ("critical", "high") for f in kept)
    assert len(kept) < len(findings), "filter dropped nothing"
    assert result["review"]["verdict"] == "request changes"


def test_min_severity_critical_only():
    result = run_review(SAMPLE_DIFF.read_text(encoding="utf-8"), demo=True,
                        min_severity="critical")
    kept = result["review"]["findings"]
    assert kept, "expected the planted critical findings to survive"
    assert all(f["severity"] == "critical" for f in kept)


def test_min_severity_rejects_unknown_level():
    with pytest.raises(ValueError):
        run_review("diff --git a/x b/x\n", demo=True, min_severity="blocker")


def test_api_min_severity():
    client = TestClient(app)
    response = client.post(
        "/review",
        json={"diff": SAMPLE_DIFF.read_text(encoding="utf-8"),
              "demo": True, "min_severity": "high"})
    assert response.status_code == 200
    kept = response.json()["review"]["findings"]
    assert kept and all(f["severity"] in ("critical", "high") for f in kept)


def test_api_min_severity_rejects_unknown_level():
    client = TestClient(app)
    response = client.post(
        "/review",
        json={"diff": "diff --git a/x b/x\n", "demo": True,
              "min_severity": "blocker"})
    assert response.status_code == 422
