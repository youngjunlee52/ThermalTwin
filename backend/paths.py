"""
파일 위치 한곳 정리 — 소스로 실행할 때와 배포판(ThermalTwin.exe, PyInstaller)으로 실행할 때 모두 맞게

  읽기 전용 자원: 학습 모델, 포함된 실차 데이터, 빌드된 화면
     소스:   프로젝트 폴더 그대로
     배포판: 실행 파일과 함께 묶인 폴더 (sys._MEIPASS)
  쓰기 위치: 로그 DB, 업로드한 실차 데이터
     소스:   프로젝트 폴더 (logs/, data/real_ev/) — v0.3 과 같음
     배포판: %LOCALAPPDATA%\\ThermalTwin  (Program Files 처럼 쓰기 금지 폴더에 풀어도 동작)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    USER_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ThermalTwin"
else:
    ROOT = Path(__file__).resolve().parent.parent
    USER_DIR = ROOT

BACKEND_DIR = ROOT / "backend"
MODEL_DIR = BACKEND_DIR / "models"
DATA_DIR = ROOT / "data" / "real_ev"                              # 포함된 실차 데이터
UPLOAD_DIR = USER_DIR / "datasets" if FROZEN else DATA_DIR       # 업로드한 데이터가 저장되는 곳
LOG_DIR = USER_DIR / "logs"
FRONTEND_DIST = ROOT / "frontend" / "dist"
TOOLS_DIR = ROOT / "tools"


def dataset_dirs() -> list[Path]:
    """실차 데이터를 찾는 폴더들 (포함 데이터 + 업로드 데이터)"""
    return list(dict.fromkeys([DATA_DIR, UPLOAD_DIR]))
