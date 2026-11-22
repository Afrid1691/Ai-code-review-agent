FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY review_agent ./review_agent
COPY examples ./examples

EXPOSE 8000

# Without OPENAI_API_KEY the API answers in demo mode; pass the key with
# `docker run -e OPENAI_API_KEY=...` for real reviews.
CMD ["uvicorn", "review_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
