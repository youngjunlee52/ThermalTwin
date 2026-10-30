"""
가상 '실제 차량' 생성기 — 디지털 트윈이 모르는 현실의 불확실성을 무작위로 만들어 냅니다.

디지털 트윈(설계값)과 실제 차량의 차이:
  1) 셀마다 내부저항 편차 (σ 6 %) + 가끔 약한 셀(저항 1.25~1.5배)   → 셀 전압으로 식별 가능
  2) 모듈별 냉각수 유량 분배가 설계와 다름 (막힘·공차, ±10 %)         → 온도센서로만 보정 가능
  3) 셀 내부 열전달: 실제 R_in 이 데이터시트보다 35 % 크고 C_core 10 % 작음 → PINN 이 데이터로 학습
  4) 노화(SOH) → 저항 증가
이 파일은 PINN 학습 데이터, 센서 배치 분석용 공분산, 시뮬레이션 차량에 모두 쓰입니다.
"""
from __future__ import annotations

import math

import numpy as np

import thermal_model as tm

TRUE_R_IN_FACTOR = 1.35
TRUE_C_CORE_FACTOR = 0.90


def random_cell_variation(rng: np.random.Generator, n: int, weak_prob: float = 0.03) -> np.ndarray:
    v = np.clip(rng.normal(1.0, 0.06, (n, tm.N_ROWS, tm.N_COLS)), 0.85, 1.2)
    weak = rng.random((n, tm.N_ROWS, tm.N_COLS)) < weak_prob
    v[weak] = rng.uniform(1.25, 1.5, int(weak.sum()))
    return v


def random_flow_share(rng: np.random.Generator, n: int) -> np.ndarray:
    share = tm.FLOW_SHARE[None, :] * np.clip(rng.normal(1.0, 0.10, (n, tm.N_ROWS)), 0.7, 1.3)
    return share / share.mean(axis=1, keepdims=True)


def make_plant(n: int, rng: np.random.Generator, t_init, soc_init, soh) -> tuple[tm.PackThermalModel, np.ndarray]:
    soh = np.broadcast_to(np.asarray(soh, dtype=float), (n,))
    plant = tm.PackThermalModel(n, t_init, soc_init, tm.CELL_CAPACITY_AH * soh)
    plant.r_scale = random_cell_variation(rng, n)
    plant.delta_decay = 1.0 - soh
    plant.r_in = plant.r_in * TRUE_R_IN_FACTOR * np.clip(rng.normal(1.0, 0.03, (n, tm.N_ROWS, tm.N_COLS)), 0.9, 1.1)
    plant.c_core = plant.c_core * TRUE_C_CORE_FACTOR
    return plant, random_flow_share(rng, n)


def drive_power_w(mode: str, t: float, phase: float = 0.0) -> float:
    """주행 모드별 배터리 출력 [W] (+방전, −회생). 주기가 다른 사인파 합성."""
    s = math.sin
    if mode == "highway":
        p = 28 + 6 * s(2 * math.pi * t / 90 + phase) + 4 * s(2 * math.pi * t / 23 + 1.0 + phase)
    elif mode == "city":
        p = 8 + 12 * s(2 * math.pi * t / 35 + phase) + 6 * s(2 * math.pi * t / 11 + 0.5 + phase)
    elif mode == "aggressive":
        p = 55 + 70 * s(2 * math.pi * t / 40 + phase) + 25 * s(2 * math.pi * t / 13 + 0.3 + phase)
    else:
        p = 0.0
    return max(-60.0, min(180.0, p)) * 1000.0


def run_ensemble(n: int, seed: int, duration_s: float = 2400.0, dt: float = 2.0, sample_every: int = 5,
                 modes: tuple[str, ...] = ("fast_charge", "fast_charge", "aggressive", "highway"),
                 power_range=(60, 200), ambient_range=(-5, 38)):
    """무작위 시나리오 n 개를 한꺼번에 시뮬레이션하고 샘플을 기록합니다.
    반환: dict(t, current(B,T), soc, ts, tc, q, coolant_in, ambient, flow_share, params...)"""
    rng = np.random.default_rng(seed)
    mode = np.array([modes[i % len(modes)] for i in range(n)])
    ambient = rng.uniform(*ambient_range, n)
    power = rng.uniform(*power_range, n)
    flow = rng.uniform(4, 25, n)
    set_c = rng.uniform(15, 30, n)
    soh = rng.uniform(0.72, 1.0, n)
    width = rng.choice([6.0, 8.0, 10.0], n)
    depth = rng.choice([2.0, 3.0, 4.0], n)
    phase = rng.uniform(0, 6.28, n)
    soc0 = np.where(mode == "fast_charge", rng.uniform(0.05, 0.3, n), rng.uniform(0.7, 0.95, n))
    rest_at = rng.uniform(0.6, 0.9, n) * duration_s   # 일부 시간 이후 휴지(냉각)
    plant, share = make_plant(n, rng, ambient, soc0, soh)
    cool = tm.cooling_plate(width, depth, flow, share)

    rec = {k: [] for k in ("t", "current", "soc", "ts", "tc", "q", "tin", "tout")}
    steps = int(duration_s / dt)
    for k in range(steps):
        t = k * dt
        p = np.zeros(n)
        for i in range(n):
            if t > rest_at[i]:
                continue
            if mode[i] == "fast_charge":
                if plant.soc[i] < 0.8:
                    p[i] = -power[i] * 1000 * float(tm.fast_charge_limit_w(np.array([plant.soc[i]]))[0])
            elif plant.soc[i] > 0.1:
                p[i] = drive_power_w(mode[i], t, phase[i])
        current = tm.current_for_power(p, plant.soc)
        plant.step(dt, current, ambient, cool, chiller_set_c=set_c)
        if k % sample_every == 0:
            rec["t"].append(t)
            rec["current"].append(current.copy())
            rec["soc"].append(plant.soc.copy())
            rec["ts"].append(plant.t_surf.copy())
            rec["tc"].append(plant.t_core.copy())
            rec["q"].append(plant.q_cell.copy())
            rec["tin"].append(plant.t_coolant_in.copy())
            rec["tout"].append(plant.t_coolant_out.copy())
    out = {k: np.array(v) for k, v in rec.items()}
    out.update(dict(mode=mode, ambient=ambient, power=power, flow=flow, set_c=set_c, soh=soh, width=width,
                    depth=depth, share=share, r_in=plant.r_in, c_core=plant.c_core, r_scale=plant.r_scale,
                    dt_sample=dt * sample_every))
    return out
