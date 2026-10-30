"""
SOH 추정 민감도 분석 — 같은 실제 SOH 에서 외부 조건(외기·냉각수 유량·충전 출력)만 바꿨을 때
추정 SOH 가 얼마나 달라지는지 확인합니다. (리뷰: 노화와 외부 환경 변화가 혼동될 가능성)

  실행: backend\\.venv\\Scripts\\python.exe backend\\soh_sensitivity.py      (약 2–4분)
  결과: backend/models/soh_sensitivity.json  (화면 R&D › PINN 모델 탭에 표시)

각 경우 시뮬레이션 차량으로 급속충전 25분(10 → 80 %)을 돌리고 마지막 추정값을 기록합니다.
  new: 현재 방식 (노화 저항 = 측정 저항 − 현재 온도의 기본 저항, 조건부·느린 갱신, 95 % 신뢰구간)
  old: v0.4 이전 방식 (25 °C 기준 저항으로 역산) — 비교용
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("THERMALTWIN_DB", "off")

from engine import Engine  # noqa: E402
from paths import MODEL_DIR  # noqa: E402

AMBIENTS = (10, 25, 40)
FLOWS = (3, 10, 20)
SOHS = (0.95, 0.85, 0.75)


def run(base: Engine, soh: float, ambient: float, flow: float, power: float = 150.0, minutes: float = 25.0) -> dict:
    e = Engine(shared=base)
    e.apply_control({"source": "sim", "mode": "fast_charge", "charge_power_kw": power, "ambient_c": ambient,
                     "coolant_flow_lpm": flow, "true_soh": soh, "derating": False, "auto_pump": False, "time_scale": 30})
    e.reset()
    f = None
    while e.t < minutes * 60:
        f = e.tick() or f
    tw = f["twin"]
    return {"true": round(soh * 100, 1), "ambient_c": ambient, "flow_lpm": flow, "power_kw": power,
            "new": tw["soh_est"], "lo": tw["soh_lo"], "hi": tw["soh_hi"], "old": tw["soh_raw"],
            "updates": tw["soh_updates"], "skipped": tw["soh_skipped"], "core_max_c": f["kpi"]["core_max_c"],
            "covered": tw["soh_lo"] is not None and tw["soh_lo"] <= soh * 100 <= tw["soh_hi"]}


def summarize(rows: list[dict], key: str) -> dict:
    errs = [r[key] - r["true"] for r in rows if r[key] is not None]
    return {"max_abs_err": round(max(abs(x) for x in errs), 2), "spread": round(max(errs) - min(errs), 2),
            "mean_err": round(sum(errs) / len(errs), 2), "n": len(errs)} if errs else {}


def main() -> dict:
    t0 = time.time()
    base = Engine()
    grid = []
    for amb in AMBIENTS:
        for flow in FLOWS:
            grid.append(run(base, 0.85, amb, flow))
            print(f"SOH 85 % · 외기 {amb} °C · 유량 {flow} L/min → 새 {grid[-1]['new']} ({grid[-1]['lo']}–{grid[-1]['hi']}) · 이전 {grid[-1]['old']}", flush=True)
    levels = [run(base, s, 25, 10) for s in SOHS]
    harsh = [run(base, 0.70, 40, 3, power=200.0)]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0, 1),
        "method": "급속충전 25분, 마지막 추정값. new = 온도 보정 역산 + 조건부 느린 갱신 + 95 % 구간, old = v0.4 이전 방식",
        "grid_soh85": grid, "levels": levels, "harsh": harsh,
        "summary": {"new": summarize(grid, "new"), "old": summarize(grid, "old"),
                    "coverage": f"{sum(r['covered'] for r in grid + levels + harsh)}/{len(grid + levels + harsh)}"},
    }
    MODEL_DIR.mkdir(exist_ok=True)
    (MODEL_DIR / "soh_sensitivity.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    r = main()
    print("요약:", json.dumps(r["summary"], ensure_ascii=False))
    print("단계별:", [(x["true"], x["new"], x["lo"], x["hi"], x["old"]) for x in r["levels"]])
    print("가혹:", [(x["true"], x["new"], x["lo"], x["hi"], x["old"], x["skipped"]) for x in r["harsh"]])
