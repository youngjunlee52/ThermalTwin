#!/bin/bash
# macOS / Linux 실행 → 브라우저에서 http://localhost:8000 (종료: Ctrl+C)
cd "$(dirname "$0")"
(sleep 3; open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null) &
backend/.venv/bin/python -m uvicorn main:app --app-dir backend --port 8000
