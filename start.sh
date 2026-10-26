#!/bin/zsh
# Sets up (first time only) and starts the Car Search Agent API on http://localhost:8000
set -e
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/python -m playwright install chromium
fi

if [[ -z "$ANTHROPIC_API_KEY" ]]; then
  echo "Note: ANTHROPIC_API_KEY is not set. The plain engine works; the ai engine will fail."
fi

echo "API docs: http://localhost:8000/docs"
exec .venv/bin/uvicorn server:app --port "${PORT:-8000}"
