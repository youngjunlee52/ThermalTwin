"""
ThermalTwin 자체 시험 — 핵심 기능과 지금까지 고친 버그가 다시 깨지지 않았는지 한 번에 확인 (약 1–2분)

  실행: 8_selftest.bat   또는   backend\\.venv\\Scripts\\python.exe tools\\selftest.py
  결과: 항목마다 ✓/✗, 하나라도 실패하면 종료 코드 1
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

os.environ["THERMALTWIN_DB"] = str(Path(tempfile.mkdtemp()) / "selftest.db")
os.environ.pop("THERMALTWIN_INGEST_TOKEN", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402

import estimator as est  # noqa: E402
import pump  # noqa: E402
import runaway  # noqa: E402
import thermal_model as tm  # noqa: E402
from engine import Engine  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
BASE = Engine()


def check(name):
    def deco(fn):
        t0 = time.time()
        try:
            msg = fn() or ""
            RESULTS.append((name, True, f"{msg} ({time.time() - t0:.1f}s)"))
        except AssertionError as e:
            RESULTS.append((name, False, str(e)))
        except Exception:  # noqa: BLE001
            RESULTS.append((name, False, traceback.format_exc(limit=3)))
        print(("✓ " if RESULTS[-1][1] else "✗ ") + name + " — " + RESULTS[-1][2], flush=True)
        return fn
    return deco


def sim(minutes=10.0, **ctl) -> tuple[Engine, dict]:
    e = Engine(shared=BASE)
    e.apply_control({"source": "sim", "mode": "fast_charge", "time_scale": 30, **ctl})
    e.reset()
    f = None
    while e.t < minutes * 60:
        f = e.tick() or f
    return e, f


TEL = {"current_a": -200, "soc": 0.5, "v_cells": [3.9] * 96, "probe_cells": [[0, 1], [3, 5], [6, 8]], "probe_t": [30.0, 31.0, 30.5]}


@check("기본 시뮬레이션: 화면 데이터 항목이 모두 있음")
def _():
    e, f = sim(5)
    for k in ("risk_status", "kpi", "field", "control", "input", "faults", "runaway", "forecast", "twin", "commands"):
        assert k in f, f"{k} 없음"
    assert 20 < f["kpi"]["core_max_c"] < 60, f"코어 {f['kpi']['core_max_c']}"
    return f"코어 최고 {f['kpi']['core_max_c']} °C, 상태 {f['risk_status']}"


@check("코어온도 추정 정확도 (정상 충전, 실제값 대비)")
def _():
    e, f = sim(15)
    rmse = f["twin"]["core_rmse_k"]
    assert rmse < 1.0, f"RMSE {rmse} K"
    return f"RMSE {rmse} K"


@check("정상 충전에서는 안전 모드가 켜지지 않음")
def _():
    e = Engine(shared=BASE)
    e.apply_control({"mode": "fast_charge", "charge_power_kw": 150, "ambient_c": 25, "time_scale": 30}); e.reset()
    on = 0
    while e.t < 1200:
        f = e.tick()
        on += bool(f and f["control"]["fallback"]["active"])
    assert on == 0, f"{on}회 켜짐"


@check("가혹 조건: 디레이팅을 꺼도 안전 모드가 온도를 막음")
def _():
    e = Engine(shared=BASE)
    e.apply_control({"mode": "fast_charge", "charge_power_kw": 200, "ambient_c": 40, "coolant_flow_lpm": 3, "true_soh": 0.7,
                     "derating": False, "auto_pump": False, "time_scale": 30}); e.reset()
    peak, fb = 0.0, 0
    while e.t < 1500:
        f = e.tick()
        if f:
            peak = max(peak, f["twin"]["true_core_max_c"])
            fb += f["control"]["fallback"]["active"]
    assert fb > 0, "안전 모드가 한 번도 켜지지 않음"
    assert peak < 56.0, f"실제 코어 최고 {peak:.1f} °C (보호 없을 때 약 73 °C)"
    return f"실제 코어 최고 {peak:.1f} °C"


@check("안전 모드: 센서 0개 · 강제 전환 · 해제")
def _():
    e = Engine(shared=BASE); e.apply_control({"mode": "fast_charge"}); e.reset(); e.set_sensors([])
    for _ in range(4):
        f = e.tick()
    assert f["control"]["mode"] == "fallback", "센서 0개인데 안전 모드 아님"
    e = Engine(shared=BASE); e.apply_control({"mode": "fast_charge"}); e.reset()
    e.apply_control({"force_fallback": True}); f = e.tick(); f = e.tick()
    assert f["control"]["mode"] == "fallback", "강제 전환 안 됨"
    e.apply_control({"force_fallback": False})
    for _ in range(6):
        f = e.tick()
    assert f["control"]["mode"] == "twin", "해제 안 됨"


@check("입력 잠금: 외부 수신 중 외기 변경 무시, 정책은 반영")
def _():
    e = Engine(shared=BASE, source="ingest")
    e.apply_control({"ambient_c": 40, "charge_power_kw": 120, "mode": "highway"})
    assert e.ctrl.ambient_c == 25.0 and e.ctrl.mode == "fast_charge", "잠금 항목이 바뀜"
    assert e.ctrl.charge_power_kw == 120.0, "정책 항목이 안 바뀜"
    e.apply_control({"source": "sim", "mode": "highway"})
    assert e.ctrl.mode == "highway", "소스 변경과 함께 모드 변경 안 됨"


@check("입력 검증: 잘못된 값 거부, 경고, 재시작 처리")
def _():
    e = Engine(shared=BASE, source="ingest")
    e.ingest(dict(TEL, t_s=0))
    for bad in (dict(TEL, t_s=10, v_cells=[3.9] * 95), dict(TEL, t_s=10, probe_t=[30.0, 999.0, 30.0]), dict(TEL, t_s=0)):
        try:
            e.ingest(bad)
            raise AssertionError(f"거부 안 됨: {list(bad)[:3]}")
        except ValueError:
            pass
    r = e.ingest(dict(TEL, t_s=20, v_cells=[3.9] * 95 + [3.4]))
    assert r["warnings"], "셀 전압 편차 경고 없음"
    e.ingest(dict(TEL, t_s=900)); r = e.ingest(dict(TEL, t_s=0))
    assert r["type"] if "type" in r else True
    assert e.consec_rejects == 0, "재시작이 거부로 처리됨"


@check("데이터 끊김: 5초 넘으면 명령 무효")
def _():
    e = Engine(shared=BASE, source="ingest"); e.ingest(dict(TEL, t_s=0)); e.ingest_state["last_rx"] -= 10
    st = e.command_status()
    assert st["stale"] and not st["valid"]


@check("[버그 수정] 재생이 끝난 뒤 다른 기록을 고르면 다시 재생됨")
def _():
    e = Engine(shared=BASE); e.apply_control({"source": "replay", "dataset": "ev_charge_strong", "time_scale": 120})
    while e.running:
        e.tick()
    e.apply_control({"dataset": "ev_charge_cold"})
    assert e.running, "멈춘 채로 있음"
    assert e.tick() is not None


@check("[버그 수정] 트윈의 냉각 모델이 실제 냉각수 유량을 따라감")
def _():
    e, f = sim(10, auto_pump=True)
    flow_twin = e.twin.design[2]
    flow_real = e.vehicle.effective_flow_lpm
    assert abs(flow_twin - flow_real) < 0.1, f"트윈 {flow_twin} vs 실제 {flow_real}"
    return f"{flow_real:.1f} L/min"


@check("펌프 한계: 낼 수 없는 유량은 낼 수 있는 만큼만 흐름")
def _():
    e, f = sim(1, channel_width_mm=4, channel_depth_mm=1, coolant_flow_lpm=25)
    eff = e.vehicle.effective_flow_lpm
    assert eff < 25, f"유량 {eff}"
    op = pump.operating_point(np.array([eff]), tm.cooling_plate(np.array([4.0]), np.array([1.0]), np.array([eff])).dp_pa)
    s = float(op["speed_ratio"][0])
    assert 0.9 < s <= 1.0, f"펌프 최대 회전수에서의 유량이 아님 (회전수 비 {s:.2f})"
    return f"요청 25 → 실제 {eff:.1f} L/min (펌프 최대 회전)"


@check("SOH: 외기·냉각 조건이 달라도 일정 (실제 85 %)")
def _():
    vals = []
    for amb, flow in ((10, 20), (40, 3)):
        e, f = sim(20, ambient_c=amb, coolant_flow_lpm=flow, true_soh=0.85, derating=False, auto_pump=False)
        vals.append(f["twin"]["soh_est"])
    assert all(v is not None and abs(v - 85) < 1.0 for v in vals), f"{vals}"
    return f"{vals}"


@check("센서 고장 진단: 차갑게 읽는 센서 격리, 정상 센서 오경보 없음")
def _():
    e = Engine(shared=BASE); e.apply_control({"mode": "fast_charge", "time_scale": 30}); e.reset()
    tgt = e.sensor_cells[8]
    orig = e.vehicle.telemetry

    def tel(idx):
        s = orig(idx)
        if s["t_s"] > 240:
            s["probe_t"] = [t - 8 if c == tgt else t for c, t in zip(s["probe_idx"], s["probe_t"])]
        return s
    e.vehicle.telemetry = tel
    while e.t < 900:
        f = e.tick() or None
    bad = f["faults"]["bad_sensors"]
    assert est.index_cell(tgt) in bad, f"격리 안 됨 {bad}"
    assert len(f["faults"]["bad_sensors_ever"]) == 1, f"오경보 {f['faults']['bad_sensors_ever']}"


@check("센서 고장 진단: 혼자 뜨거운 센서는 버리지 않음 (냉각 불량 셀)")
def _():
    e = Engine(shared=BASE); e.apply_control({"mode": "fast_charge", "time_scale": 30, "derating": False}); e.reset()
    cell = next(s for s in e.sensor_cells if 0 < est.index_cell(s)[0] < 7 and 0 < est.index_cell(s)[1] < 11)
    r, c = divmod(cell, tm.N_COLS)
    e.vehicle.plant.cell_cool_scale[0, r, c] = 0.1
    under = 0.0
    while e.t < 1200:
        f = e.tick()
        if f:
            core = max(f["field"]["core"][r][c], f["field"]["core_nn"][r][c])
            under = max(under, f["field"]["true_core"][r][c] - core)
    assert est.index_cell(cell) not in f["faults"]["bad_sensors"], "뜨거운 센서를 격리함"
    assert under < 1.5, f"냉각 불량 셀을 {under:.1f} K 낮게 봄"
    return f"최대 과소 {under:.2f} K"


@check("열폭주 모델: 개시온도·임계온도 범위")
def _():
    assert 75 < runaway.T_ONSET_C < 95, runaway.T_ONSET_C
    tc = runaway.semenov_critical_c(0.16 + tm.R_TIM)
    assert 100 < tc < 130, tc


@check("냉각판 설계: 운전 가능 설계만으로 추천, 손실 민감도 5경우")
def _():
    from design_sweep import run_design_sweep
    r = run_design_sweep(150, 25, 20, 0.85, current_design=(8, 3, 10))
    assert r["recommend_3d"] and r["recommend_3d"]["feasible"]
    assert 0 < r["n_feasible"] < r["n_cases"]
    assert len(r["loss_sensitivity"]) == 5
    rec = r["recommend_3d"]
    return f"추천 {rec['width_mm']}·{rec['depth_mm']}·{rec['flow_lpm']} ({r['n_feasible']}/{r['n_cases']} 운전 가능)"


@check("실차 재생 3구간: 숨긴 프로브 예측이 평균값보다 정확")
def _():
    out = []
    for ds in ("ev_charge_strong", "ev_charge_cold", "ev_drive_long"):
        e = Engine(shared=BASE); e.apply_control({"source": "replay", "dataset": ds, "time_scale": 120})
        f = None
        while e.running:
            f = e.tick() or f
        rp = f["replay"]
        assert rp["holdout_rmse_k"] < rp["mean_baseline_rmse_k"], f"{ds}: {rp['holdout_rmse_k']} ≥ {rp['mean_baseline_rmse_k']}"
        assert not f["faults"]["bad_sensors_ever"], f"{ds}: 센서 오경보 {f['faults']['bad_sensors_ever']}"
        out.append(rp["holdout_rmse_k"])
    return f"{out} K"


@check("서버 API 모듈 로드·다중 차량 우선순위 규칙")
def _():
    import main
    main.engine.user_source_hold = False
    r = main.ingest_one(dict(TEL, t_s=0))
    assert r["vehicle_id"] == "default" and main.engine.ctrl.source == "ingest"
    main.engine.apply_control({"source": "sim"}); main.engine.user_source_hold = True
    r = main.ingest_one(dict(TEL, t_s=10))
    assert r["vehicle_id"] == "external" and main.engine.ctrl.source == "sim", r.get("vehicle_id")


if __name__ == "__main__":
    ok = sum(1 for _, v, _ in RESULTS if v)
    print(f"\n결과: {ok}/{len(RESULTS)} 통과")
    sys.exit(0 if ok == len(RESULTS) else 1)
