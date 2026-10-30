"""
시뮬레이션 차량 — 실제 차량 대신 '진짜 값'을 가진 가상 차량. BMS 처럼 텔레메트리를 내보내고 제어 명령을 받습니다.

BMS 측정값 (실제 차량과 같은 형태):
  - 팩 전류, SOC, 셀 전압 96개 (±2 mV 잡음, 1 mV 분해능)
  - 온도센서 N개 (위치 자유, ±0.25 °C 잡음, 0.1 °C 분해능)
  - 냉각수 입구온도, 외기온도
받는 제어 명령: 충전 출력 상한(kW), 냉각수 유량(L/min), 냉각수 목표온도(°C)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import scenarios as sc
import thermal_model as tm


@dataclass
class VehicleControls:
    mode: str = "fast_charge"
    charge_power_kw: float = 150.0
    ambient_c: float = 25.0
    coolant_set_c: float = 20.0
    coolant_flow_lpm: float = 10.0
    channel_width_mm: float = 8.0
    channel_depth_mm: float = 3.0
    true_soh: float = 0.85


MODE_START_SOC = {"fast_charge": 0.10, "highway": 0.90, "city": 0.90, "aggressive": 0.90, "rest": 0.50}
CHARGE_TARGET_SOC = 0.80
DRIVE_MIN_SOC = 0.10


class SimVehicle:
    def __init__(self, seed: int = 7):
        self.seed = seed
        self.ctrl = VehicleControls()
        self.power_limit_kw: float | None = None   # 트윈/BMS 가 보내는 충전 출력 상한
        self.reset()

    def reset(self) -> None:
        c = self.ctrl
        rng = np.random.default_rng(self.seed)
        self.rng = np.random.default_rng(self.seed + 1000)
        self.plant, self.flow_share = sc.make_plant(1, rng, c.ambient_c, MODE_START_SOC[c.mode], c.true_soh)
        # 시연용: 약한 셀 하나를 고정 위치에 둠 (기본 센서 사이)
        self.plant.r_scale[0, 5, 6] = 1.4
        self.mode = c.mode
        self.t = 0.0
        self.note = ""
        self.power_w = 0.0
        self.current = 0.0
        self._cool_key = None
        self.effective_flow_lpm = self.ctrl.coolant_flow_lpm   # 펌프 한계를 반영한 실제 유량

    def set_soh(self, soh: float) -> None:
        self.ctrl.true_soh = soh
        self.plant.delta_decay[:] = 1.0 - soh
        self.plant.capacity_ah[:] = tm.CELL_CAPACITY_AH * soh

    def cooling(self) -> tm.CoolingResult:
        """요청 유량이 펌프 최대 성능을 넘으면, 펌프 곡선과 배관 저항이 만나는 유량(최대 회전수)만 흐름"""
        c = self.ctrl
        key = (c.channel_width_mm, c.channel_depth_mm, c.coolant_flow_lpm)
        if key != self._cool_key:
            flow = c.coolant_flow_lpm
            cool = tm.cooling_plate(np.array([key[0]]), np.array([key[1]]), np.array([flow]), self.flow_share)
            if float(cool.pump["speed_ratio"][0]) > 1.0:
                grid = np.arange(0.1, flow + 1e-9, 0.1)
                s = tm.cooling_plate(np.full(len(grid), key[0]), np.full(len(grid), key[1]), grid).pump["speed_ratio"]
                ok = s <= 1.0
                flow = float(grid[ok].max()) if ok.any() else 0.1
                cool = tm.cooling_plate(np.array([key[0]]), np.array([key[1]]), np.array([flow]), self.flow_share)
            self.effective_flow_lpm = flow
            self._cool = cool
            self._cool_key = key
        return self._cool

    def requested_power_w(self) -> float:
        soc = float(self.plant.soc[0])
        if self.mode == "fast_charge":
            if soc >= CHARGE_TARGET_SOC:
                self.mode, self.note = "rest", f"충전 완료 ({CHARGE_TARGET_SOC * 100:.0f} %까지 충전) — 쉬는 중"
                return 0.0
            p = self.ctrl.charge_power_kw * float(tm.fast_charge_limit_w(np.array([soc]))[0])
            if self.power_limit_kw is not None:
                p = min(p, self.power_limit_kw)
            if float(self.plant.t_core.min()) < 10.0:        # 저온 충전 보호 (BMS 내부 로직)
                p *= 0.3
            return -p * 1000.0
        if self.mode in ("highway", "city", "aggressive"):
            if soc <= DRIVE_MIN_SOC:
                self.mode, self.note = "rest", f"배터리 {DRIVE_MIN_SOC * 100:.0f} % 남아 주행 종료 — 쉬는 중"
                return 0.0
            p = sc.drive_power_w(self.mode, self.t)
            if self.power_limit_kw is not None and p > 0:
                p = min(p, self.power_limit_kw * 1000.0 * 1.2)
            return p
        return 0.0

    def step(self, dt: float = 1.0) -> None:
        p = self.requested_power_w()
        self.current = float(tm.current_for_power(np.array([p]), self.plant.soc)[0])
        self.power_w = p
        self.plant.step(dt, np.array([self.current]), np.array([self.ctrl.ambient_c]), self.cooling(),
                        chiller_set_c=np.array([self.ctrl.coolant_set_c]))
        self.t += dt

    def telemetry(self, probe_idx: list[int]) -> dict:
        p = self.plant
        v = p.cell_voltages(np.array([self.current]))[0].ravel()
        v = np.round(v + self.rng.normal(0, 0.002, v.shape), 3)
        ts = p.t_surf[0].ravel()
        probes = ts[probe_idx] + self.rng.normal(0, 0.25, len(probe_idx))
        return {
            "t_s": self.t, "current_a": self.current, "soc": float(p.soc[0]),
            "v_cells": v.tolist(), "probe_idx": list(probe_idx), "probe_t": np.round(probes, 1).tolist(),
            "t_coolant_in": round(float(p.t_coolant_in[0]), 2), "t_ambient": self.ctrl.ambient_c,
            "coolant_flow_lpm": self.effective_flow_lpm,    # BMS 는 펌프 명령(유량)을 알고 있음
            "speed_kmh": None,
        }

    def truth(self) -> dict:
        p = self.plant
        return {"core": p.t_core[0].ravel().copy(), "surf": p.t_surf[0].ravel().copy(),
                "q": p.q_cell[0].ravel().copy(), "coolant_out": float(p.t_coolant_out[0]),
                "chiller_w": float(p.chiller_w[0]), "q_cool": float(p.q_coolant_cell.sum())}
