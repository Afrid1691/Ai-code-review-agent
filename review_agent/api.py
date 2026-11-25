"""FastAPI app exposing the review pipeline over HTTP.

Run it with:
    uvicorn review_agent.api:app --reload

Then:
    curl -X POST localhost:8000/review -H 'Content-Type: application/json' \
        -d '{"diff": "<unified diff text>", "demo": true}'
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .graph import run_review

app = FastAPI(
    title="AI Code Review Agent",
    description="Multi-agent pull request reviews, powered by LangGraph.",
    version="1.1.0",
)


class ReviewRequest(BaseModel):
    diff: str = Field(..., description="Unified diff text to review")
    demo: bool = Field(
        False,
        description="Use the rule-based demo model. Also used automatically "
                    "when OPENAI_API_KEY is not set on the server.")
    source: str = Field("", description="Optional label for the change, "
                                        "e.g. the PR URL")
    min_severity: str | None = Field(
        None, pattern="^(critical|high|medium|low)$",
        description="Only report findings at this severity or above. "
                    "The verdict follows the filtered set.")


class ReviewResponse(BaseModel):
    demo: bool
    review: dict
    markdown: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/review", response_model=ReviewResponse)
def review(request: ReviewRequest) -> ReviewResponse:
    result = run_review(request.diff, demo=request.demo, source=request.source,
                        min_severity=request.min_severity)
    return ReviewResponse(**result)
