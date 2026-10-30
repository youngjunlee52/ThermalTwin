"""
열폭주 반응열 모델 — 45/55 °C 같은 고정 한계값 대신, 코어온도에서 '스스로 열이 나기 시작하는' 정도를 계산

반응: SEI(음극 피막) 분해 — 열폭주 연쇄의 첫 단계 (Feng et al. 2018 리뷰의 1단계, 약 80~120 °C)
  반응속도  r = A · exp(−Ea / (R·T)) · c_sei                     (Hatchard et al. 2001, JES 148 A755)
  자기발열  Q_self = H_sei · m_anode · r   [W]
  파라미터  A = 1.667e15 1/s, Ea = 1.3508e5 J/mol, H_sei = 257 J/g, c_sei0 = 0.15   (Hatchard 2001 / Kim 2007)
            m_anode = 셀 질량의 30 % (가정 — 셀 사양서의 음극 활물질량으로 바꿔야 함)

판정 지표 (셀마다)
  1) 단열 유도시간 t_ad = m·cp · (R·T²/Ea) / Q_self
     냉각이 완전히 끊겼을 때 자기발열이 폭주하기까지 걸리는 시간 (Frank-Kamenetskii 단열 근사)
  2) Semenov 안정성: dQ_self/dT < 1/R_th  (냉각이 살아 있으면 자기발열 증가를 냉각이 이기는가)
     R_th = 코어→냉각수 열저항 (PINN 이 학습한 R_in + 갭필러 R_TIM)
  3) 발열 개시온도(ARC 기준 0.02 K/min)까지 남은 여유 [K]
한계: SEI 재생성·노화에 따른 c_sei 변화, 분리막 붕괴(~130 °C)·양극 분해 등 다음 단계는 넣지 않았습니다.
"""
from __future__ import annotations

import numpy as np

import thermal_model as tm

R_GAS = 8.314
A_SEI = 1.667e15
EA_SEI = 1.3508e5
H_SEI_J_PER_G = 257.0
C_SEI0 = 0.15
ANODE_MASS_G = 0.30 * tm.CELL_MASS_KG * 1000.0
CELL_HEAT_CAP = tm.CELL_MASS_KG * tm.CELL_CP          # [J/K]
ONSET_RATE_K_PER_MIN = 0.02                          # ARC 발열 개시 판정 기준

ADIABATIC_WARNING_H = 24.0
ADIABATIC_DANGER_H = 1.0


def self_heating_w(t_core_c: np.ndarray) -> np.ndarray:
    t_k = np.asarray(t_core_c, dtype=float) + 273.15
    return H_SEI_J_PER_G * ANODE_MASS_G * A_SEI * np.exp(-EA_SEI / (R_GAS * t_k)) * C_SEI0


def adiabatic_induction_h(t_core_c: np.ndarray) -> np.ndarray:
    t_k = np.asarray(t_core_c, dtype=float) + 273.15
    q = np.maximum(self_heating_w(t_core_c), 1e-30)
    return CELL_HEAT_CAP * (R_GAS * t_k**2 / EA_SEI) / q / 3600.0


def onset_temperature_c() -> float:
    """단열 자기발열 속도가 0.02 K/min 이 되는 온도 (이분법)"""
    target_w = ONSET_RATE_K_PER_MIN / 60.0 * CELL_HEAT_CAP
    lo, hi = 20.0, 250.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if self_heating_w(np.array(mid)) < target_w:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def semenov_critical_c(r_th: float) -> float:
    """냉각이 정상일 때 자기발열 증가율이 냉각 증가율(1/R_th)을 넘는 온도"""
    lo, hi = 20.0, 300.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        t_k = mid + 273.15
        slope = float(self_heating_w(np.array(mid))) * EA_SEI / (R_GAS * t_k**2)
        if slope < 1.0 / r_th:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


T_ONSET_C = onset_temperature_c()


def assess(core_c: np.ndarray, r_in: float) -> dict:
    core_c = np.asarray(core_c, dtype=float)
    i = int(np.argmax(core_c))
    t_max = float(core_c[i])
    q = self_heating_w(core_c)
    t_ad = float(adiabatic_induction_h(np.array(t_max)))
    r_th = r_in + tm.R_TIM
    t_crit = semenov_critical_c(r_th)
    if t_ad < ADIABATIC_DANGER_H or t_max >= t_crit:
        level = "Danger"
    elif t_ad < ADIABATIC_WARNING_H:
        level = "Warning"
    else:
        level = "Safe"
    return {
        "level": level,
        "hottest_cell": i,
        "self_heat_max_w": float(q.max()),
        "self_heat_total_w": float(q.sum()),
        "adiabatic_hours": round(min(t_ad, 1e6), 2),
        "onset_c": round(T_ONSET_C, 1),
        "margin_to_onset_k": round(T_ONSET_C - t_max, 1),
        "semenov_critical_c": round(t_crit, 1),
        "cooled_stable": t_max < t_crit,
    }
