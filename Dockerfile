FROM python:3.12-slim

WORKDIR /app

# Install docker CLI / system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e ".[ai]"

CMD ["python", "-m", "opspilot.main"]
