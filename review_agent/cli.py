"""Command line entry point.

Examples:
    python -m review_agent --pr https://github.com/owner/repo/pull/42
    python -m review_agent --diff examples/sample_pr.diff --demo
    python -m review_agent --pr <url> --post-comment --output review.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .github_client import fetch_pr_diff, post_pr_comment
from .graph import run_review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review_agent",
        description="Review a pull request diff with a team of AI agents.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", metavar="URL",
                        help="GitHub pull request URL to review")
    source.add_argument("--diff", metavar="PATH",
                        help="Path to a local unified diff file")
    parser.add_argument("--demo", action="store_true",
                        help="Use the built-in rule-based demo model "
                             "(no API key needed). Also the default when "
                             "OPENAI_API_KEY is not set.")
    parser.add_argument("--post-comment", action="store_true",
                        help="Post the review as a comment on the PR "
                             "(requires --pr and GITHUB_TOKEN)")
    parser.add_argument("--output", metavar="PATH",
                        help="Write the Markdown review to this file")
    parser.add_argument("--json", action="store_true",
                        help="Print the review as JSON instead of Markdown")
    parser.add_argument("--min-severity", metavar="LEVEL",
                        choices=["critical", "high", "medium", "low"],
                        help="Only report findings at this severity or "
                             "above; the verdict follows the filtered set")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.pr:
        try:
            diff = fetch_pr_diff(args.pr)
        except Exception as exc:  # network and URL errors land here
            print(f"Could not fetch the pull request diff: {exc}",
                  file=sys.stderr)
            return 1
        source_label = args.pr
    else:
        diff = Path(args.diff).read_text(encoding="utf-8")
        source_label = args.diff

    result = run_review(diff, demo=args.demo, source=source_label,
                        min_severity=args.min_severity)

    if result["demo"]:
        print("Note: running in demo mode (rule-based stand-in model, no "
              "API key used). Set OPENAI_API_KEY for real model reviews.",
              file=sys.stderr)

    if args.json:
        print(json.dumps(result["review"], indent=2))
    else:
        print(result["markdown"])

    if args.output:
        Path(args.output).write_text(result["markdown"], encoding="utf-8")
        print(f"Markdown review written to {args.output}", file=sys.stderr)

    if args.post_comment:
        if not args.pr:
            print("--post-comment only works together with --pr",
                  file=sys.stderr)
            return 1
        try:
            url = post_pr_comment(args.pr, result["markdown"])
        except Exception as exc:
            print(f"Could not post the comment: {exc}", file=sys.stderr)
            return 1
        print(f"Review posted: {url}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
