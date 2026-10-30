"""
냉각수 펌프 + 냉각 루프 모델 — 펌프가 실제로 낼 수 있는 유량·압력인지, 그때 전기를 얼마나 쓰는지

[가정값 — 실제 부품 사양서로 바꿔야 함]
  펌프: 차량용 12 V BLDC 전동 워터펌프 급 (최대 회전수에서)
        최대 양정 H0 = 60 kPa (유량 0), 최대 유량 Q_MAX = 40 L/min (양정 0), 최고효율 유량 = 0.55·Q_MAX
        성능곡선 Δp = s²·H0 − H0·(Q/Q_MAX)²   (s = 회전수 비, 상사법칙)
        수력효율 최고 45 %, 모터·드라이버 효율 60–85 % (저속일수록 낮음), 최소 회전수 20 %
  루프: 칠러 열교환기·호스·퀵커넥터 등 냉각판 밖의 손실 Δp_loop = 18 kPa × (Q / 20 L/min)²

운전점: 시스템 저항(냉각판 + 루프) 곡선과 펌프 곡선이 만나는 회전수 s 를 구함. s > 1 이면 이 펌프로 불가능.
"""
from __future__ import annotations

import numpy as np

H0_PA = 60e3
Q_MAX_LPM = 40.0
Q_BEP_RATIO = 0.55
ETA_HYD_PEAK = 0.45
S_MIN = 0.2
LOOP_DP_REF_PA = 18e3
LOOP_Q_REF_LPM = 20.0
PLATE_DP_LIMIT_PA = 100e3     # 냉각판(브레이징 알루미늄) 허용 압력강하 가정


def loop_dp_pa(q_lpm) -> np.ndarray:
    q = np.asarray(q_lpm, dtype=float)
    return LOOP_DP_REF_PA * (q / LOOP_Q_REF_LPM) ** 2


def operating_point(q_lpm, dp_plate_pa) -> dict:
    """벡터 입력. 반환: 필요 회전수 비 s, 효율, 전기 소비, 운전 가능 여부와 이유"""
    q = np.asarray(q_lpm, dtype=float)
    dp_plate = np.asarray(dp_plate_pa, dtype=float)
    dp_total = dp_plate + loop_dp_pa(q)
    s = np.sqrt((dp_total + H0_PA * (q / Q_MAX_LPM) ** 2) / H0_PA)
    s_run = np.maximum(s, S_MIN)
    x = q / (s_run * Q_BEP_RATIO * Q_MAX_LPM)                     # 최고효율점 대비 유량 비
    eta_h = np.clip(ETA_HYD_PEAK * (1.0 - 0.8 * (x - 1.0) ** 2), 0.05, ETA_HYD_PEAK)
    eta_m = 0.60 + 0.25 * np.clip(s_run, 0.0, 1.0)
    hyd_w = dp_total * q / 60000.0
    electric_w = hyd_w / (eta_h * eta_m)
    feasible = (s <= 1.0) & (dp_plate <= PLATE_DP_LIMIT_PA)
    reason = np.where(s > 1.0, "펌프 최대 성능 초과", np.where(dp_plate > PLATE_DP_LIMIT_PA, "냉각판 허용 압력 초과", ""))
    return {"dp_total_pa": dp_total, "dp_loop_pa": dp_total - dp_plate, "speed_ratio": s, "eta_hyd": eta_h,
            "eta_motor": eta_m, "eta_total": eta_h * eta_m, "electric_w": electric_w, "feasible": feasible,
            "reason": reason}


def pump_curve(n: int = 41) -> dict:
    """화면 표시용: 최대 회전수 펌프 곡선"""
    q = np.linspace(0, Q_MAX_LPM, n)
    return {"q_lpm": q.round(2).tolist(), "dp_kpa": (H0_PA * (1 - (q / Q_MAX_LPM) ** 2) / 1000).round(2).tolist()}
