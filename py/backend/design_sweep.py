"""
냉각판 설계 공간 탐색 — Jiang et al.(2024) 다목적 최적화 아이디어를 단순화

- 설계변수: 채널 폭 w, 채널 깊이 d, 냉각수 유량
- 목적함수(모두 최소화): ① 급속충전 중 최고 코어온도 ② 셀 간 최대 온도편차 ③ 펌프 전기 소비
- 제약: 펌프가 실제로 낼 수 있는 운전점일 것 (pump.py 성능곡선, 냉각판 + 루프 압력), 냉각판 허용 압력
- 압력강하 = 직관 마찰 + 180° 굽힘 국부 손실 + 입구·출구 손실 (+ 루프: 칠러·호스)
- 방법: 모든 조합(격자)을 한 번에 벡터 계산 → 운전 가능한 설계 중 3목적 파레토 → 무릎점 추천
  추천이 압력손실 모델 오차에 흔들리지 않는지 손실계수를 바꿔 다시 확인 (손실 민감도)
  ※ 논문은 CFD 가 비싸서 베이지안 최적화(MOBO)로 표본 수를 줄였지만,
    여기서는 계산이 가벼운 축약 모델이라 전수 탐색이 가능합니다. 최종 후보는 CFD 로 검증해야 합니다 (cfd_plan).
"""
from __future__ import annotations

import itertools
import time

import numpy as np

import pump as pm
import thermal_model as tm

DT_TARGET_K = 5.0     # 셀 간 온도편차 목표 (Jiang 2024 등에서 흔히 쓰는 5 K)
LOSS_CASES = [
    ("기준", {}),
    ("마찰 +30 %", {"friction": 1.3}),
    ("굽힘 손실 ×0.5", {"bend": 0.5}),
    ("굽힘 손실 ×2", {"bend": 2.0}),
    ("입출구 손실 ×3", {"port": 3.0}),
]


def pareto_nd(obj: np.ndarray) -> np.ndarray:
    """obj: (N, k) 최소화. 파레토 최적이면 True."""
    n = obj.shape[0]
    mask = np.ones(n, dtype=bool)
    for i in range(n):
        dominated = np.all(obj <= obj[i], axis=1) & np.any(obj < obj[i], axis=1)
        if dominated.any():
            mask[i] = False
    return mask


def knee(obj: np.ndarray, ids: np.ndarray, log_cols=(2,)) -> int:
    """정규화(펌프 동력은 로그) 후 이상점(0,…)에 가장 가까운 설계"""
    o = obj[ids].astype(float).copy()
    for c in log_cols:
        o[:, c] = np.log10(np.maximum(o[:, c], 1e-6))
    lo, hi = o.min(axis=0), o.max(axis=0)
    norm = (o - lo) / np.where(hi - lo > 1e-9, hi - lo, 1.0)
    return int(ids[int(np.argmin(np.linalg.norm(norm, axis=1)))])


def evaluate_pump_side(w, d, f, peak_core, peak_dt, scale: dict) -> dict:
    """열 결과는 유량만의 함수라 그대로 두고, 압력손실 배율만 바꿔 펌프 쪽(운전 가능·동력)과 추천을 다시 계산"""
    saved = dict(tm.LOSS_SCALE)
    tm.LOSS_SCALE.update(scale)
    try:
        cool = tm.cooling_plate(w, d, f)
    finally:
        tm.LOSS_SCALE.clear()
        tm.LOSS_SCALE.update(saved)
    feas = cool.pump["feasible"]
    ids = np.nonzero(feas)[0]
    obj = np.stack([peak_core, peak_dt, cool.pump_electric_w], axis=1)
    p3 = np.zeros(len(w), dtype=bool)
    if len(ids):
        p3[ids] = pareto_nd(obj[ids])
    rec = knee(obj, np.nonzero(p3)[0]) if p3.any() else None
    return {"cool": cool, "feasible": feas, "pareto3": p3, "recommend": rec}

WIDTHS_MM = [4, 5, 6, 7, 8, 9, 10]
DEPTHS_MM = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
FLOWS_LPM = [5, 10, 15, 20, 25]


def run_design_sweep(charge_power_kw: float, ambient_c: float, coolant_set_c: float, soh: float,
                     current_design: tuple[float, float, float] | None = None,
                     target_soc: float = 0.80, start_soc: float = 0.10) -> dict:
    t_start = time.perf_counter()
    combos = list(itertools.product(WIDTHS_MM, DEPTHS_MM, FLOWS_LPM))
    current_id = None
    if current_design is not None:
        if tuple(float(x) for x in current_design) in {tuple(map(float, c)) for c in combos}:
            current_id = [tuple(map(float, c)) for c in combos].index(tuple(float(x) for x in current_design))
        else:
            combos.append(tuple(current_design))
            current_id = len(combos) - 1
    w = np.array([c[0] for c in combos], dtype=float)
    d = np.array([c[1] for c in combos], dtype=float)
    f = np.array([c[2] for c in combos], dtype=float)
    b = len(combos)

    cool = tm.cooling_plate(w, d, f)
    model = tm.PackThermalModel(b, ambient_c, start_soc, tm.CELL_CAPACITY_AH * soh)
    model.delta_decay[:] = 1.0 - soh

    amb = np.full(b, ambient_c)
    set_c = np.full(b, coolant_set_c)
    peak_core = np.full(b, -np.inf)
    peak_dt = np.zeros(b)
    dt, t, t_end = 2.0, 0.0, None
    while t < 4 * 3600:
        active = model.soc < target_soc
        if not active.any():
            if t_end is None:
                t_end = t
            break
        p = -charge_power_kw * 1000.0 * tm.fast_charge_limit_w(model.soc) * active
        current = tm.current_for_power(p, model.soc)
        model.step(dt, current, amb, cool, chiller_set_c=set_c)
        peak_core = np.maximum(peak_core, model.t_core.max(axis=(1, 2)))
        peak_dt = np.maximum(peak_dt, model.t_surf.max(axis=(1, 2)) - model.t_surf.min(axis=(1, 2)))
        t += dt
    charge_time_s = t_end if t_end is not None else t

    feas = cool.pump["feasible"]
    feas_ids = np.nonzero(feas)[0]

    def front(metric: np.ndarray) -> tuple[np.ndarray, int]:
        """운전 가능한 설계 중 (지표, 펌프 동력) 2목적 파레토와 무릎점"""
        obj = np.stack([metric, cool.pump_electric_w], axis=1)
        mask = np.zeros(b, dtype=bool)
        mask[feas_ids] = pareto_nd(obj[feas_ids])
        return mask, knee(obj, np.nonzero(mask)[0], log_cols=(1,))

    mask_t, knee_t = front(peak_core)
    mask_d, knee_d = front(peak_dt)

    # 3목적 (최고온도·온도편차·펌프 동력) + 손실 모델 민감도
    sens = []
    base_eval = None
    for label, scale in LOSS_CASES:
        ev = evaluate_pump_side(w, d, f, peak_core, peak_dt, scale)
        base_eval = base_eval or ev
        r = ev["recommend"]
        sens.append({"case": label, "n_feasible": int(ev["feasible"].sum()),
                     "recommend": None if r is None else {"width_mm": float(w[r]), "depth_mm": float(d[r]), "flow_lpm": float(f[r])},
                     "base_rec_still_feasible": bool(ev["feasible"][base_eval["recommend"]]) if base_eval["recommend"] is not None else None,
                     "base_rec_still_pareto": bool(ev["pareto3"][base_eval["recommend"]]) if base_eval["recommend"] is not None else None})
    p3, rec3 = base_eval["pareto3"], base_eval["recommend"]

    # 이전 방식 추천 (운전 가능 여부·루프 손실 무시, 고정 효율 35 %) — 비교용
    old_pw = cool.pumping_w / tm.PUMP_EFFICIENCY
    old_obj = np.stack([peak_core, old_pw], axis=1)
    old_mask = pareto_nd(old_obj)
    old_knee = knee(old_obj, np.nonzero(old_mask)[0], log_cols=(1,))

    op = cool.pump
    points = []
    for i in range(b):
        points.append({
            "id": i,
            "width_mm": float(w[i]), "depth_mm": float(d[i]), "flow_lpm": float(f[i]),
            "peak_core_c": round(float(peak_core[i]), 2),
            "peak_delta_t_k": round(float(peak_dt[i]), 2),
            "pump_w": round(float(cool.pump_electric_w[i]), 2),
            "pumping_w": round(float(cool.pumping_w[i]), 3),
            "dp_kpa": round(float(cool.dp_pa[i]) / 1000, 2),
            "dp_total_kpa": round(float(op["dp_total_pa"][i]) / 1000, 2),
            "dp_friction_kpa": round(float(cool.dp_friction_pa[i]) / 1000, 2),
            "dp_bend_kpa": round(float(cool.dp_bend_pa[i]) / 1000, 2),
            "dp_port_kpa": round(float(cool.dp_port_pa[i]) / 1000, 2),
            "pump_speed": round(float(op["speed_ratio"][i]), 2),
            "pump_eta": round(float(op["eta_total"][i]), 3),
            "feasible": bool(feas[i]), "infeasible_reason": str(op["reason"][i]),
            "reynolds": round(float(cool.reynolds[i].mean()), 0),
            "h": round(float(cool.h[i].mean()), 0),
            "pump_energy_wh": round(float(cool.pump_electric_w[i]) * charge_time_s / 3600, 2),
            "pareto_tmax": bool(mask_t[i]),
            "pareto_dt": bool(mask_d[i]),
            "pareto_3d": bool(p3[i]),
            "dt_ok": bool(peak_dt[i] <= DT_TARGET_K),
        })

    def brief(i):
        if i is None:
            return None
        p = points[i]
        return {k: p[k] for k in ("id", "width_mm", "depth_mm", "flow_lpm", "peak_core_c", "peak_delta_t_k", "pump_w",
                                  "dp_total_kpa", "pump_speed", "feasible", "infeasible_reason")}

    # CFD 검증 후보: 3목적 추천, 운전 가능한 설계 중 최저 최고온도, 최저 온도편차
    cand = {"3목적 추천": rec3,
            "최저 최고온도 (운전 가능)": int(feas_ids[np.argmin(peak_core[feas_ids])]) if len(feas_ids) else None,
            "최저 온도편차 (운전 가능)": int(feas_ids[np.argmin(peak_dt[feas_ids])]) if len(feas_ids) else None}
    cfd_plan = {
        "candidates": [{"role": k, **brief(v)} for k, v in cand.items() if v is not None],
        "compare": ["냉각판 압력강하 (축약 모델 vs CFD, 굽힘·입출구 손실 비중 포함)",
                    "모듈 내 채널별 유량 분배 (매니폴드 불균일)",
                    "급속충전 중 셀 최고 표면온도·셀 간 온도편차 (CHT, 셀 발열은 이 앱의 발열 이력 사용)",
                    "대류 열전달계수 h (Shah & London·Hausen 상관식 vs CFD)"],
        "setup": "모듈 냉각판 1개(서펜타인 3병렬) + 셀 12개 단순화 형상, 공액 열전달(CHT), 층류/천이 확인 (Re 표시값), "
                 "격자 수렴 3단계, 입구 유량·온도는 이 앱의 운전점 사용. 도구 예: ANSYS Fluent · STAR-CCM+ · OpenFOAM(chtMultiRegionFoam)",
        "accept": "압력강하 ±20 %, 최고온도 ±1 K, 온도편차 ±1 K 이내면 축약 모델 추천 유지. 벗어나면 손실·열전달 상관식을 CFD 로 보정 후 재탐색",
    }
    return {
        "pump_model": {"h0_kpa": pm.H0_PA / 1000, "q_max_lpm": pm.Q_MAX_LPM, "eta_hyd_peak": pm.ETA_HYD_PEAK,
                       "loop_dp_kpa_at_20lpm": pm.LOOP_DP_REF_PA / 1000, "plate_dp_limit_kpa": pm.PLATE_DP_LIMIT_PA / 1000,
                       "curve": pm.pump_curve(), "assumed": True},
        "n_feasible": int(feas.sum()),
        "dt_target_k": DT_TARGET_K,
        "n_dt_ok_feasible": int(np.sum(feas & (peak_dt <= DT_TARGET_K))),
        "recommend_3d": brief(rec3),
        "old_recommend": brief(old_knee),
        "loss_sensitivity": sens,
        "cfd_plan": cfd_plan,
        "conditions": {
            "charge_power_kw": charge_power_kw, "ambient_c": ambient_c,
            "coolant_set_c": coolant_set_c, "soh": soh,
            "start_soc": start_soc, "target_soc": target_soc,
        },
        "charge_time_min": round(charge_time_s / 60, 1),
        "n_cases": b,
        "current_id": current_id,
        "knee_tmax_id": knee_t,
        "knee_dt_id": knee_d,
        "elapsed_s": round(time.perf_counter() - t_start, 2),
        "points": points,
    }
