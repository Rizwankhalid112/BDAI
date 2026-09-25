#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

docker compose up -d
python -m app.db.init_db

python -m app.worker.worker &
WORKER_PID=$!
trap 'echo "Stopping worker..."; kill $WORKER_PID 2>/dev/null; wait $WORKER_PID 2>/dev/null' EXIT

streamlit run ui/streamlit_app.py
