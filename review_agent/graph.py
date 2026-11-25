"""LangGraph orchestration for the review team.

The graph fans the diff out to three specialist reviewers in parallel,
then joins at the lead reviewer, which deduplicates and ranks everything
the specialists found and writes the final verdict.

    START ─┬─> bug_hunter ──┐
           ├─> security ────┼─> lead_reviewer ─> END
           └─> style ───────┘

Merging and ranking happen in plain code rather than in a model call:
deduplication and severity ordering should be deterministic, and the
tests lean on that. The model is only asked to write the prose summary.
"""

from __future__ import annotations

import os
import re
from typing import Any, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from .agents import SEVERITY_ORDER, lead_summary, run_reviewer
from .demo_llm import DemoChatModel

AGENT_NAMES = ("bug_hunter", "security", "style")


class ReviewState(TypedDict, total=False):
    diff: str
    source: str
    min_severity: str
    bug_hunter_findings: list[dict[str, Any]]
    security_findings: list[dict[str, Any]]
    style_findings: list[dict[str, Any]]
    review: dict[str, Any]
    markdown: str


def get_llm(demo: bool = False) -> BaseChatModel:
    """Return the demo model, or ChatOpenAI when a real key is available.

    Demo mode is chosen explicitly with ``demo=True``, and automatically
    when OPENAI_API_KEY is not set, so the pipeline always runs.
    """
    if demo or not os.environ.get("OPENAI_API_KEY"):
        return DemoChatModel()
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        temperature=0,
    )


def using_demo(llm: BaseChatModel) -> bool:
    return isinstance(llm, DemoChatModel)


def _reviewer_node(agent: str, llm: BaseChatModel):
    def node(state: ReviewState) -> dict[str, Any]:
        findings = run_reviewer(llm, agent, state["diff"])
        return {f"{agent}_findings": findings}

    return node


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop near-duplicates, keeping the highest severity report."""
    best: dict[tuple[str, int, str], dict[str, Any]] = {}
    for finding in findings:
        key_words = re.sub(r"[^a-z0-9 ]", "", finding["title"].lower())
        key = (finding["file"], finding["line"],
               " ".join(key_words.split()[:4]))
        current = best.get(key)
        if current is None or (SEVERITY_ORDER[finding["severity"]]
                               < SEVERITY_ORDER[current["severity"]]):
            best[key] = finding
    return list(best.values())


def _rank(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        findings,
        key=lambda f: (SEVERITY_ORDER[f["severity"]], f["file"], f["line"]),
    )


def _verdict(findings: list[dict[str, Any]]) -> str:
    if any(f["severity"] in ("critical", "high") for f in findings):
        return "request changes"
    return "approve"


def _lead_node(llm: BaseChatModel):
    def node(state: ReviewState) -> dict[str, Any]:
        merged: list[dict[str, Any]] = []
        for agent in AGENT_NAMES:
            merged.extend(state.get(f"{agent}_findings", []))
        findings = _rank(_dedupe(merged))
        min_severity = state.get("min_severity")
        if min_severity:
            ceiling = SEVERITY_ORDER[min_severity]
            findings = [f for f in findings
                        if SEVERITY_ORDER[f["severity"]] <= ceiling]
        verdict = _verdict(findings)
        summary = lead_summary(llm, findings)
        counts = {sev: 0 for sev in SEVERITY_ORDER}
        for finding in findings:
            counts[finding["severity"]] += 1
        review = {
            "source": state.get("source", ""),
            "verdict": verdict,
            "summary": summary,
            "counts": counts,
            "findings": findings,
        }
        return {"review": review, "markdown": render_markdown(review)}

    return node


def render_markdown(review: dict[str, Any]) -> str:
    """Render a finished review dict as a Markdown document."""
    verdict = review["verdict"]
    header = "## Code Review" + (
        f" — {review['source']}" if review.get("source") else "")
    lines = [header, ""]
    lines.append(f"**Verdict: {'Request changes' if verdict == 'request changes' else 'Approve'}**")
    lines.append("")
    lines.append(review["summary"])
    lines.append("")
    counts = review["counts"]
    count_text = ", ".join(
        f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low")
        if counts.get(s))
    lines.append(f"**Findings:** {len(review['findings'])} "
                 f"({count_text or 'none'})")
    lines.append("")
    agent_labels = {
        "bug_hunter": "Bug Hunter",
        "security": "Security",
        "style": "Style & Quality",
        "lead_reviewer": "Lead Reviewer",
    }
    for idx, finding in enumerate(review["findings"], start=1):
        location = finding["file"] or "unknown file"
        if finding["line"]:
            location += f":{finding['line']}"
        lines.append(
            f"### {idx}. [{finding['severity'].upper()}] {finding['title']}")
        lines.append(
            f"*{location} — reported by {agent_labels.get(finding['agent'], finding['agent'])}*")
        lines.append("")
        lines.append(finding["description"])
        lines.append("")
        lines.append(f"**Suggested fix:** {finding['suggestion']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_graph(llm: BaseChatModel):
    """Compile the review graph around a given chat model."""
    builder = StateGraph(ReviewState)
    for agent in AGENT_NAMES:
        builder.add_node(agent, _reviewer_node(agent, llm))
        builder.add_edge(START, agent)
    builder.add_node("lead_reviewer", _lead_node(llm))
    for agent in AGENT_NAMES:
        builder.add_edge(agent, "lead_reviewer")
    builder.add_edge("lead_reviewer", END)
    return builder.compile()


def run_review(diff: str, demo: bool = False, source: str = "",
               min_severity: str | None = None) -> dict[str, Any]:
    """Run the full review pipeline over a diff and return the result.

    The result dict has ``review`` (verdict, summary, ranked findings),
    ``markdown`` (the rendered review), and ``demo`` (True when the
    rule-based demo model produced the findings).

    ``min_severity`` ("critical", "high", "medium" or "low") drops
    findings below that level before the verdict is computed, so the
    verdict follows the filtered set: a diff whose only findings are
    low-severity approves when the floor is "high".
    """
    if min_severity is not None and min_severity not in SEVERITY_ORDER:
        raise ValueError(
            f"unknown severity {min_severity!r}; expected one of "
            f"{', '.join(SEVERITY_ORDER)}")
    llm = get_llm(demo=demo)
    graph = build_graph(llm)
    result = graph.invoke({"diff": diff, "source": source,
                           "min_severity": min_severity})
    return {
        "demo": using_demo(llm),
        "review": result["review"],
        "markdown": result["markdown"],
    }
