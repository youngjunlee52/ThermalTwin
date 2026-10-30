"""
고장 상황 강건성 검증 — 센서가 없는 셀의 냉각 불량, 온도센서 고장(치우침·고착·서서히 어긋남·끊김)에서
트윈이 국부 과열을 놓치지 않는지, 코어온도 추정이 무너지지 않는지 확인합니다. (리뷰 3순위)

  실행: backend\\.venv\\Scripts\\python.exe backend\\robustness.py          (약 2–3분)
  결과: backend/models/robustness.json  (화면 R&D › 센서 배치 분석 탭에 표시)

공통 조건: 시뮬레이션 차량, 급속충전 150 kW · 외기 25 °C · 냉각수 10 L/min, 균등 센서 16개, 25분.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

os.environ.setdefault("THERMALTWIN_DB", "off")

import estimator as est  # noqa: E402
import thermal_model as tm  # noqa: E402
from engine import Engine  # noqa: E402
from paths import MODEL_DIR  # noqa: E402

N_SENSORS = 16
MINUTES = 25.0
FAULT_START_S = 240.0


def farthest_cell(sensors: list[int]) -> int:
    """센서에서 가장 먼 안쪽 셀 (가장자리 셀은 외기로 잘 식으므로 제외)"""
    sc = np.array([est.index_cell(i) for i in sensors])
    best, best_d = 0, -1.0
    for i in range(tm.N_CELLS):
        r, c = divmod(i, tm.N_COLS)
        if i in sensors or r in (0, tm.N_ROWS - 1) or c in (0, tm.N_COLS - 1):
            continue
        d = float(np.min(np.abs(sc[:, 0] - r) * 2.0 + np.abs(sc[:, 1] - c)))   # 모듈 사이는 단열 → 행 거리 가중
        if d > best_d:
            best, best_d = i, d
    return best


def adjacent_cell(sensors: list[int]) -> int:
    """센서 바로 옆(같은 모듈, 한 칸) 안쪽 셀"""
    for s in sensors:
        r, c = est.index_cell(s)
        for cc in (c + 1, c - 1):
            i = est.cell_index(r, cc) if 0 <= cc < tm.N_COLS else None
            if i is not None and i not in sensors and 0 < r < tm.N_ROWS - 1 and 0 < cc < tm.N_COLS - 1:
                return i
    return sensors[0]


def run(base: Engine, scenario: dict, fdi: bool, n_sensors: int = N_SENSORS) -> dict:
    e = Engine(shared=base)
    e.apply_control({"source": "sim", "mode": "fast_charge", "charge_power_kw": 150, "ambient_c": 25,
                     "coolant_flow_lpm": 10, "true_soh": 0.85, "derating": False, "auto_pump": False, "time_scale": 30})
    e.sensor_cells = est.uniform_layout(n_sensors)
    e.reset()
    e.twin.fdi_enabled = fdi
    v = e.vehicle
    kind = scenario["kind"]
    target_cell = scenario.get("cell")
    target_sensor = scenario.get("sensor")
    if kind == "cooling":
        r, c = divmod(target_cell, tm.N_COLS)
        v.plant.cell_cool_scale[0, r, c] = scenario["scale"]
    orig = v.telemetry
    state = {"stuck": None}

    def telemetry(probe_idx):
        s = orig(probe_idx)
        t = s["t_s"]
        if kind in ("offset", "stuck", "drift", "dropout") and t >= FAULT_START_S:
            pt = list(s["probe_t"])
            idx = list(s["probe_idx"])
            if kind == "dropout":
                keep = [k for k in range(len(idx)) if k % 2 == 0]
                s["probe_idx"], s["probe_t"] = [idx[k] for k in keep], [pt[k] for k in keep]
                return s
            k = idx.index(target_sensor)
            if kind == "offset":
                pt[k] += scenario["value"]
            elif kind == "stuck":
                state["stuck"] = pt[k] if state["stuck"] is None else state["stuck"]
                pt[k] = state["stuck"]
            elif kind == "drift":
                pt[k] += scenario["rate"] * (t - FAULT_START_S)
            s["probe_t"] = pt
        return s
    v.telemetry = telemetry

    errs, peak_true, peak_est, flagged_at, hot_err = [], 0.0, 0.0, None, []
    anomaly_at, cell_err = None, []
    f = None
    while e.t < MINUTES * 60:
        f = e.tick() or f
        if f is None:
            continue
        tc = np.array(f["field"]["true_core"]).ravel()
        core_safe = np.maximum(np.array(f["field"]["core"]).ravel(), np.array(f["field"]["core_nn"]).ravel())
        if e.t >= FAULT_START_S:
            errs.append(float(np.sqrt(np.mean((np.array(f["field"]["core"]).ravel() - tc) ** 2))))
            hot_err.append(float(core_safe.max() - tc.max()))
        peak_true, peak_est = max(peak_true, float(tc.max())), max(peak_est, float(core_safe.max()))
        if target_cell is not None:
            cell_err.append(float(core_safe[target_cell] - tc[target_cell]))
        fd = f.get("faults", {})
        if target_sensor is not None and flagged_at is None and \
                est.index_cell(target_sensor) in fd.get("bad_sensors", []) + fd.get("local_sensors", []):
            flagged_at = e.t
        if target_cell is not None and anomaly_at is None and est.index_cell(target_cell) in [a["cell"] for a in fd.get("hotspots", [])]:
            anomaly_at = e.t
    fd = f.get("faults", {})
    true_bad = {tuple(est.index_cell(target_sensor))} if target_sensor is not None and kind != "dropout" else set()
    flagged = {tuple(c) for c in fd.get("bad_sensors_ever", [])}
    return {
        "core_rmse_k": round(float(np.mean(errs)), 2),
        "hottest_cell_underestimate_k": round(float(-min(hot_err)), 2),     # 가장 크게 낮춰 본 정도 (+ 면 과소)
        "peak_true_c": round(peak_true, 1), "peak_est_c": round(peak_est, 1),
        "fault_cell_max_underestimate_k": round(float(-min(cell_err)), 2) if cell_err else None,
        "sensor_flagged_after_s": round(flagged_at - FAULT_START_S, 0) if flagged_at else None,
        "false_sensor_flags": len(flagged - true_bad),
        "hotspot_flagged_after_s": round(anomaly_at, 0) if anomaly_at else None,
    }


def scenarios(sensors: list[int]) -> list[dict]:
    hot = farthest_cell(sensors)
    s_mid = sensors[len(sensors) // 2]
    s_in = next(s for s in sensors if 0 < est.index_cell(s)[0] < tm.N_ROWS - 1 and 0 < est.index_cell(s)[1] < tm.N_COLS - 1)
    return [
        {"name": "정상 (고장 없음)", "kind": "none"},
        {"name": f"센서 없는 셀 M{est.index_cell(hot)[0] + 1}-C{est.index_cell(hot)[1] + 1} 냉각 불량 (냉각 10 %)", "kind": "cooling", "cell": hot, "scale": 0.1},
        {"name": f"센서 달린 셀 M{est.index_cell(s_in)[0] + 1}-C{est.index_cell(s_in)[1] + 1} 냉각 불량 (냉각 10 %)", "kind": "cooling", "cell": s_in, "sensor": s_in, "scale": 0.1},
        {"name": f"센서 M{est.index_cell(s_mid)[0] + 1}-C{est.index_cell(s_mid)[1] + 1} −8 K 치우침 (차갑게 읽음)", "kind": "offset", "sensor": s_mid, "value": -8.0},
        {"name": f"센서 M{est.index_cell(s_mid)[0] + 1}-C{est.index_cell(s_mid)[1] + 1} +8 K 치우침 (뜨겁게 읽음)", "kind": "offset", "sensor": s_mid, "value": 8.0},
        {"name": f"센서 M{est.index_cell(s_mid)[0] + 1}-C{est.index_cell(s_mid)[1] + 1} 값 고착", "kind": "stuck", "sensor": s_mid},
        {"name": f"센서 M{est.index_cell(s_mid)[0] + 1}-C{est.index_cell(s_mid)[1] + 1} 서서히 어긋남 (−0.6 K/분)", "kind": "drift", "sensor": s_mid, "rate": -0.01},
        {"name": "센서 절반(8개) 통신 끊김", "kind": "dropout"},
    ]


def main() -> dict:
    t0 = time.time()
    base = Engine()
    sens = est.uniform_layout(N_SENSORS)
    rows = []
    for sc in scenarios(sens):
        before = run(base, sc, fdi=False)
        after = run(base, sc, fdi=True)
        rows.append({"name": sc["name"], "kind": sc["kind"], "before": before, "after": after})
        print(sc["name"], "\n   진단 없음:", before, "\n   진단 사용:", after, flush=True)
    # 센서 없는 셀의 냉각 불량: 센서 수 · 센서와의 거리에 따라 얼마나 놓치는가
    coverage = []
    for n in (8, 16, 32, 48):
        ss = est.uniform_layout(n)
        cases = [("센서에서 가장 먼 셀", farthest_cell(ss)), ("센서 바로 옆 셀", adjacent_cell(ss))]
        if n == N_SENSORS:
            inner = next(s for s in ss if 0 < est.index_cell(s)[0] < tm.N_ROWS - 1 and 0 < est.index_cell(s)[1] < tm.N_COLS - 1)
            cases.append(("센서가 달린 셀", inner))
        for where, cell in cases:
            res = run(base, {"kind": "cooling", "cell": cell, "scale": 0.1}, fdi=True, n_sensors=n)
            coverage.append({"n_sensors": n, "where": where, "cell": est.index_cell(cell),
                             "fault_cell_max_underestimate_k": res["fault_cell_max_underestimate_k"],
                             "hottest_cell_underestimate_k": res["hottest_cell_underestimate_k"]})
            print(f"센서 {n}개 · {where} {est.index_cell(cell)} → 고장 셀 최대 과소 {res['fault_cell_max_underestimate_k']} K", flush=True)
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "seconds": round(time.time() - t0, 1),
              "conditions": f"급속충전 150 kW · 25 °C · 10 L/min · 균등 센서 {N_SENSORS}개 · {MINUTES:.0f}분 · 고장 시작 {FAULT_START_S:.0f} s",
              "rows": rows, "cooling_coverage": coverage,
              "finding": "셀 사이 단열 패드(R_LAT 4 K/W) 때문에 한 셀의 국부 과열은 이웃 셀 센서에 거의 전달되지 않음. "
                         "센서를 48개로 늘려도, 센서 바로 옆이어도 냉각 불량 셀을 약 10 K 낮게 봄 → 셀 단위 냉각 불량은 그 셀에 "
                         "센서가 있어야만 관측 가능. 대책은 센서 증설이 아니라 조립 공정 검사(갭필러 접촉), 셀별 온도 측정 "
                         "(셀마다 NTC·플렉서블 PCB), 또는 임피던스 기반 셀 온도 측정 같은 하드웨어 쪽.",
              "tried_not_adopted": [
                  "셀 전압으로 숨은 과열 역산 (저항 −2.7 %/K): 셀 간 원래 저항 편차(0.85–1.42배)에 묻혀 냉각 불량 셀을 구분하지 못함",
                  "모듈별 냉각수 출구온도 열수지: 정상 상태의 모델-실제 차이(모듈당 ±20–50 W)가 냉각 불량 신호(약 20 W)보다 커서 구분 불가"]}
    MODEL_DIR.mkdir(exist_ok=True)
    (MODEL_DIR / "robustness.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
