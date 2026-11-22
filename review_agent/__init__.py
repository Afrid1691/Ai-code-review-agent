"""AI Code Review Agent.

A LangGraph team of reviewer agents (bug hunting, security, style) whose
findings are merged and ranked by a lead reviewer into one pull request
review. See graph.py for the pipeline and demo_llm.py for the keyless
demo mode.
"""

from .graph import run_review

__all__ = ["run_review"]
__version__ = "1.0.0"
