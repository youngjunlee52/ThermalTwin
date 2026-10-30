"""
웹 데모(GitHub Pages) 빌드 — 설치 없이 주소만 열면 브라우저에서 ThermalTwin 이 돌아가는 정적 사이트

  python tools/build_web_demo.py [출력 폴더]      (기본: web_demo/)

frontend 를 VITE_WEB_DEMO=1 로 빌드하고, 엔진 코드(backend/*.py)·학습 모델·실차 데이터를 py/ 아래에 복사합니다.
브라우저에서는 public/pyworker.js 가 Pyodide 로 이 파일들을 그대로 실행합니다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "web_demo"
MODULES = ["webapi", "engine", "estimator", "ingest_guard", "runaway", "thermal_model", "pinn", "sim_vehicle",
           "scenarios", "store", "paths", "twin", "fleet", "design_sweep", "pump"]

if OUT.exists():
    shutil.rmtree(OUT)
npm = "npm.cmd" if os.name == "nt" else "npm"
env = {**os.environ, "VITE_WEB_DEMO": "1"}
subprocess.run([npm, "run", "build", "--", "--outDir", str(OUT), "--emptyOutDir"], cwd=ROOT / "frontend", env=env, check=True)

files: list[str] = []


def put(src: Path, rel: str) -> None:
    dst = OUT / "py" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    files.append(rel)


for m in MODULES:
    put(ROOT / "backend" / f"{m}.py", f"backend/{m}.py")
for f in sorted((ROOT / "backend" / "models").iterdir()):
    if f.is_file() and not f.name.startswith("."):
        put(f, f"backend/models/{f.name}")
for f in sorted((ROOT / "data" / "real_ev").iterdir()):
    if f.suffix in (".csv", ".json", ".txt"):
        put(f, f"data/real_ev/{f.name}")
(OUT / "py" / "manifest.json").write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
(OUT / ".nojekyll").write_text("", encoding="utf-8")
print(f"웹 데모 빌드 완료: {OUT}  (엔진 파일 {len(files)}개)")
