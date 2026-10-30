#!/bin/bash
# macOS / Linux 설치 (한 번만)
set -e
cd "$(dirname "$0")"
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install --upgrade pip
backend/.venv/bin/python -m pip install -r backend/requirements.txt
# 선택: CAN 게이트웨이 (python-can, cantools). 실패해도 설치는 계속
backend/.venv/bin/python -m pip install -r tools/requirements-can.txt || echo "[경고] CAN 게이트웨이 패키지 설치 실패 — tools/can_gateway.py 에만 필요"
echo "설치 완료! 이제 ./start_mac.sh 를 실행하세요."
