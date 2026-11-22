"""The specialist reviewer agents.

Each agent is a thin wrapper around a chat model: it sends the diff with
its role prompt, parses the JSON findings out of the reply, and stamps
every finding with the agent's name. Parsing is defensive because model
output is not a contract: fences are stripped, and a reply that is not a
JSON list simply yields no findings instead of crashing the review.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from . import prompts

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

AGENT_PROMPTS = {
    "bug_hunter": prompts.BUG_HUNTER_SYSTEM,
    "security": prompts.SECURITY_SYSTEM,
    "style": prompts.STYLE_SYSTEM,
}


def _extract_json_list(content: str) -> list[dict[str, Any]]:
    """Pull a JSON list of findings out of a model reply."""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            return []
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def run_reviewer(llm: BaseChatModel, agent: str, diff: str) -> list[dict[str, Any]]:
    """Run one specialist reviewer over a diff and return its findings."""
    messages = prompts.reviewer_prompt(AGENT_PROMPTS[agent], diff)
    reply = llm.invoke(messages)
    findings = _extract_json_list(str(reply.content))
    cleaned = []
    for finding in findings:
        severity = str(finding.get("severity", "low")).lower()
        if severity not in SEVERITY_ORDER:
            severity = "low"
        cleaned.append({
            "agent": agent,
            "title": str(finding.get("title", "Untitled finding")),
            "severity": severity,
            "file": str(finding.get("file", "")),
            "line": int(finding.get("line") or 0),
            "description": str(finding.get("description", "")),
            "suggestion": str(finding.get("suggestion", "")),
        })
    return cleaned


def lead_summary(llm: BaseChatModel, findings: list[dict[str, Any]]) -> str:
    """Ask the lead reviewer model for the opening summary paragraph."""
    reply = llm.invoke(prompts.lead_prompt(findings))
    text = str(reply.content).strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("summary"):
            return str(data["summary"])
    except json.JSONDecodeError:
        pass
    return text
