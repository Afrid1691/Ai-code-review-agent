"""Deterministic, rule-based stand-in for a chat model.

The demo model lets the whole review pipeline run end to end with no API
key and no network. It reads the same prompts the real model receives,
works out which reviewer is asking, and answers by scanning the diff with
a small set of pattern rules (string-formatted SQL, committed secrets,
dynamic code execution with ``eval``/``exec``, bare ``except:`` blocks,
unchecked ``None`` returns, and a few style smells).

The responses are plain JSON in exactly the shape the agents ask for, so
nothing downstream can tell the difference. Output is fully deterministic:
the same diff always produces the same findings.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """Return (file, new_line_number, text) for every added line in a diff."""
    results: list[tuple[str, int, str]] = []
    current_file = ""
    new_line_no = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:].strip()
            continue
        if raw.startswith("+++ "):
            current_file = raw[4:].strip()
            continue
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if hunk:
            new_line_no = int(hunk.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            results.append((current_file, new_line_no, raw[1:]))
            new_line_no += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line_no += 1
    return results


def _finding(agent: str, title: str, severity: str, file: str, line: int,
             description: str, suggestion: str) -> dict[str, Any]:
    return {
        "agent": agent,
        "title": title,
        "severity": severity,
        "file": file,
        "line": line,
        "description": description,
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# Rule-based scanners, one per reviewer
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|access[_-]?token)\s*=\s*['\"][^'\"]{8,}['\"]"
)


def _scan_security(lines: list[tuple[str, int, str]]) -> list[dict[str, Any]]:
    findings = []
    for file, lineno, text in lines:
        if "execute(" in text and ("%" in text or ".format(" in text
                                    or 'f"' in text or "f'" in text):
            findings.append(_finding(
                "security",
                "SQL injection via string-formatted query",
                "critical", file, lineno,
                "The query is built by formatting user-controlled values "
                "directly into the SQL string. An attacker can break out of "
                "the quoted value and run arbitrary SQL.",
                "Use a parameterized query: cursor.execute("
                "\"SELECT ... WHERE name = %s\", (name,)) and let the "
                "database driver handle escaping.",
            ))
        secret = _SECRET_RE.search(text)
        if secret:
            findings.append(_finding(
                "security",
                "Hardcoded secret committed to source code",
                "critical", file, lineno,
                f"The {secret.group(1)} is assigned a literal value in the "
                "source. Anyone with repo access (and anyone the repo leaks "
                "to) now has the credential, and it lives in git history "
                "even after it is removed.",
                "Read the value from an environment variable or a secrets "
                "manager, and rotate the exposed credential.",
            ))
        if "pickle.loads(" in text or "pickle.load(" in text:
            findings.append(_finding(
                "security",
                "Unsafe deserialization with pickle",
                "high", file, lineno,
                "pickle can execute arbitrary code while loading, so "
                "untrusted input must never reach it.",
                "Use a safe format such as JSON for untrusted data.",
            ))
        if re.search(r"\byaml\.load\(", text) and "SafeLoader" not in text:
            findings.append(_finding(
                "security",
                "yaml.load without SafeLoader",
                "high", file, lineno,
                "yaml.load with the default loader can construct arbitrary "
                "Python objects from untrusted input.",
                "Use yaml.safe_load, or pass Loader=yaml.SafeLoader.",
            ))
        if "shell=True" in text:
            findings.append(_finding(
                "security",
                "subprocess call with shell=True",
                "high", file, lineno,
                "shell=True passes the command through a shell, so any "
                "interpolated value becomes a command-injection risk.",
                "Pass the command as a list of arguments and drop shell=True.",
            ))
        dyn_exec = re.search(r"\b(eval|exec)\s*\(", text)
        if dyn_exec:
            findings.append(_finding(
                "security",
                "Dynamic code execution with "
                f"{dyn_exec.group(1)}()",
                "high", file, lineno,
                "eval() and exec() run whatever string they are given as "
                "Python code, so any value that reaches them from outside "
                "the codebase becomes arbitrary code execution.",
                "Do not evaluate caller-supplied strings. Parse the input "
                "into a fixed set of operations instead (for example a "
                "mapping of named filters), or use ast.literal_eval for "
                "plain data.",
            ))
    return findings


def _scan_bugs(diff: str, lines: list[tuple[str, int, str]]) -> list[dict[str, Any]]:
    findings = []
    text_by_line = {(file, lineno): text for file, lineno, text in lines}

    for file, lineno, text in lines:
        if re.match(r"\s*except\s*:\s*(#.*)?$", text):
            findings.append(_finding(
                "bug_hunter",
                "Bare except clause swallows all errors",
                "high", file, lineno,
                "A bare `except:` catches everything, including "
                "KeyboardInterrupt and real bugs, and the empty handler "
                "hides the failure completely. Callers see a normal return "
                "and have no way to tell something went wrong.",
                "Catch the specific exceptions you expect (for example "
                "ValueError or requests.RequestException), log the error, "
                "and re-raise or return an explicit failure value.",
            ))
        if re.search(r"def\s+\w+\([^)]*=\s*(\[\]|\{\})", text):
            findings.append(_finding(
                "bug_hunter",
                "Mutable default argument",
                "medium", file, lineno,
                "A list or dict used as a default argument is created once "
                "and shared between every call, so mutations leak from one "
                "call into the next.",
                "Default to None and create the list or dict inside the "
                "function body.",
            ))
        if "range(len(" in text and "+ 1" in text:
            findings.append(_finding(
                "bug_hunter",
                "Possible off-by-one in range over a sequence",
                "medium", file, lineno,
                "range(len(items) + 1) produces one index past the end of "
                "the sequence, which will raise IndexError or process a "
                "phantom element.",
                "Iterate over the items directly, or use enumerate() when "
                "the index is needed.",
            ))

    # Unchecked None return: a helper in the diff returns None on a miss,
    # and a caller dereferences the result without a guard.
    if "return None" in diff:
        assign_re = re.compile(r"(\w+)\s*=\s*\w+\([^)]*\)\s*$")
        for file, lineno, text in lines:
            match = assign_re.search(text.strip())
            if not match:
                continue
            var = match.group(1)
            for later_no in range(lineno + 1, lineno + 8):
                later = text_by_line.get((file, later_no), "")
                if re.search(rf"\b{re.escape(var)}\s*(\.|\[)", later):
                    if any(f"if {var}" in text_by_line.get((file, n), "")
                           for n in range(lineno + 1, later_no)):
                        break
                    findings.append(_finding(
                        "bug_hunter",
                        "Possible None dereference: return value used "
                        "without a None check",
                        "high", file, later_no,
                        f"`{var}` comes from a function that returns None "
                        "when there is no result, and it is dereferenced on "
                        f"the next line with no `if {var}` guard. A missing "
                        "record turns into an AttributeError at runtime.",
                        f"Check for None first: `if {var} is None: ...` "
                        "and handle the missing case explicitly.",
                    ))
                    break
    return findings


def _scan_style(lines: list[tuple[str, int, str]]) -> list[dict[str, Any]]:
    findings = []
    for idx, (file, lineno, text) in enumerate(lines):
        def_match = re.match(r"(\s*)def\s+(\w+)\s*\(", text)
        if def_match and not def_match.group(2).startswith("_"):
            following = lines[idx + 1][2] if idx + 1 < len(lines) else ""
            if '"""' not in following and "'''" not in following:
                findings.append(_finding(
                    "style",
                    f"Public function '{def_match.group(2)}' has no docstring",
                    "low", file, lineno,
                    "Public functions without a docstring force readers to "
                    "reverse-engineer the contract from the body, which "
                    "slows down every future change.",
                    "Add a one-line docstring that says what the function "
                    "returns and when it returns None or raises.",
                ))
        if re.search(r"^\s*print\(", text):
            findings.append(_finding(
                "style",
                "Debug print left in production code",
                "low", file, lineno,
                "A print() call writes straight to stdout, bypassing the "
                "logging setup and making the output impossible to filter "
                "or silence in production.",
                "Use the module logger (logger.debug/info) instead.",
            ))
    return findings


def _lead_summary(findings_json: str) -> str:
    try:
        findings = json.loads(findings_json)
    except (json.JSONDecodeError, TypeError):
        findings = []
    counts: dict[str, int] = {}
    for finding in findings:
        sev = finding.get("severity", "low")
        counts[sev] = counts.get(sev, 0) + 1
    parts = [f"{counts[s]} {s}" for s in ("critical", "high", "medium", "low")
             if counts.get(s)]
    if not findings:
        return ("No blocking issues found. The changes look safe to merge; "
                "the reviewers raised nothing above a nit.")
    top = findings[0]
    return (
        f"This change needs work before it merges: {', '.join(parts)} "
        f"finding(s) across the three reviewers. The most urgent item is "
        f"\"{top.get('title')}\" in {top.get('file')}. Fix the critical and "
        "high severity items, address or reply to the rest, and the change "
        "should be in good shape for another pass."
    )


# ---------------------------------------------------------------------------
# The chat model itself
# ---------------------------------------------------------------------------


class DemoChatModel(BaseChatModel):
    """A chat model that answers review prompts with rule-based findings."""

    @property
    def _llm_type(self) -> str:  # pragma: no cover - trivial accessor
        return "demo-rule-based"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = "\n".join(str(m.content) for m in messages)
        role_match = re.search(r"ROLE:\s*(\w+)", prompt)
        role = role_match.group(1) if role_match else ""
        diff_match = re.search(r"DIFF:\n(.*)", prompt, re.DOTALL)
        diff = diff_match.group(1) if diff_match else ""

        if role == "lead_reviewer":
            findings_match = re.search(r"FINDINGS:\n(.*)", prompt, re.DOTALL)
            payload = findings_match.group(1) if findings_match else "[]"
            content = json.dumps({"summary": _lead_summary(payload)})
        else:
            lines = added_lines(diff)
            if role == "security":
                findings = _scan_security(lines)
            elif role == "bug_hunter":
                findings = _scan_bugs(diff, lines)
            elif role == "style":
                findings = _scan_style(lines)
            else:
                findings = []
            content = json.dumps(findings)

        message = AIMessage(content=content)
        return ChatResult(generations=[ChatGeneration(message=message)])
