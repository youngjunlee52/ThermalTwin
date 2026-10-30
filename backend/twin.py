"""
디지털 트윈 추정기 — 시뮬레이션 차량·실차 로그·외부 실시간 데이터에 똑같이 동작

입력 (텔레메트리 1 샘플):
  t_s, current_a(+방전), soc(0~1), v_cells(96, 선택), probe_idx/probe_t(임의 개수·위치의 온도센서),
  t_coolant_in(선택), t_ambient(선택)
처리 순서:
  ① 셀 전압 → 셀별 저항(RLS) → 셀별 발열 q_i            (센서 위치와 무관)
  ② 물리 모델 전파 (R_in, C_core 는 PINN 이 학습한 값)
  ③ 온도센서 오차 → 학습 공분산(GP)으로 96셀 보정          (어떤 센서 배치든 자동 대응)
  ④ PINN 가상센서로 셀별 코어온도 추정
"""
from __future__ import annotations

import math

import numpy as np

import thermal_model as tm
from estimator import CellResistanceRLS, SensorFaultMonitor, SOHEstimator, SpatialGP
from pinn import PINNVirtualSensor

N = tm.N_CELLS


class TwinEstimator:
    def __init__(self, width_mm=8.0, depth_mm=3.0, flow_lpm=10.0, sensor_noise=0.3,
                 pinn: PINNVirtualSensor | None = None, gp_cov: np.ndarray | None = None,
                 use_voltage: bool = True, use_gp: bool = True, ocv_known: bool = True, k_prior: float = 1.0):
        self.ocv_known, self.k_prior = ocv_known, k_prior
        self.core_gain = 0.6 if ocv_known else 0.2
        self.pinn = pinn or PINNVirtualSensor()
        self.gp = SpatialGP(gp_cov, sensor_noise)
        self.use_voltage, self.use_gp = use_voltage, use_gp
        self.fdi_enabled = True           # 센서 고장 진단·격리
        self.set_design(width_mm, depth_mm, flow_lpm)
        self.reset()

    def set_design(self, width_mm, depth_mm, flow_lpm) -> None:
        self.design = (float(width_mm), float(depth_mm), float(flow_lpm))
        self.cool = tm.cooling_plate(np.array([width_mm]), np.array([depth_mm]), np.array([flow_lpm]))

    def set_flow(self, flow_lpm: float) -> None:
        """실제 냉각수 유량이 바뀌면(펌프 자동 제어·안전 모드) 트윈의 냉각 모델도 같은 유량으로"""
        w, d, f = self.design
        if abs(float(flow_lpm) - f) > 0.05:
            self.set_design(w, d, flow_lpm)

    def reset(self) -> None:
        self.model: tm.PackThermalModel | None = None
        self.rls = CellResistanceRLS(k_prior=self.k_prior, ocv_known=self.ocv_known)
        self.soh = SOHEstimator()
        self.fdi = SensorFaultMonitor()
        self.probe_idx_all: list[int] = []
        self.pinn.reset()
        self.t_prev = None
        self.t_boundary = None        # 냉각수 입구온도를 모를 때 학습하는 경계온도
        self.core_pinn = None
        self.last_residual = np.zeros(0)
        self.samples = 0

    def _init_model(self, t0: float, soc: float) -> None:
        self.model = tm.PackThermalModel(1, t0, soc)
        if self.pinn.ok:   # PINN 이 데이터에서 찾은 실제 열전달 파라미터로 트윈 보정
            self.model.r_in[:] = self.pinn.R_in
            self.model.c_core[:] = self.pinn.C
        self.t_boundary = t0
        self.core_pinn = np.full(N, t0)

    def step(self, s: dict) -> dict:
        t = float(s["t_s"])
        current = float(s["current_a"])
        soc = float(np.clip(s["soc"], 0.0, 1.0))
        probe_idx = list(s.get("probe_idx") or [])
        probe_t = np.asarray(s.get("probe_t") or [], dtype=float)
        if self.model is None:
            t0 = float(np.mean(probe_t)) if len(probe_t) else float(s.get("t_ambient") or 25.0)
            self._init_model(t0, soc)
            self.t_prev = t
        m = self.model
        if s.get("coolant_flow_lpm") is not None:
            self.set_flow(float(s["coolant_flow_lpm"]))
        dt = max(0.0, min(60.0, t - self.t_prev))
        self.t_prev = t
        m.soc[:] = soc

        # ① 셀 전압 → 셀별 저항
        rb = tm.r_base(m.t_core[0], np.array(soc)).ravel()
        if self.use_voltage and s.get("v_cells") is not None:
            self.rls.update(np.asarray(s["v_cells"], dtype=float), current, rb, soc)
            k = self.rls.k_cells
        else:
            self.rls.last_r_meas = None
            k = np.full(N, self.rls.k_mean)
        m.r_abs = (k * rb).reshape(1, tm.N_ROWS, tm.N_COLS)

        # ② 물리 모델 전파
        t_in = s.get("t_coolant_in")
        t_in = float(t_in) if t_in is not None else float(self.t_boundary)
        t_amb = s.get("t_ambient")
        t_amb = float(t_amb) if t_amb is not None else t_in
        if dt > 0:
            n_sub = max(1, math.ceil(dt / 2.0))
            for _ in range(n_sub):
                m.step(dt / n_sub, np.array([current]), np.array([t_amb]), self.cool,
                       t_coolant_in_meas=np.array([t_in]), update_soc=False)

        # ③ 온도센서 보정 (임의 개수·위치). 고장 진단으로 이상한 센서는 빼고 보정
        self.probe_idx_all = probe_idx
        local: list[tuple[int, float]] = []
        if probe_idx and self.use_gp and self.fdi_enabled:
            used = self.fdi.update(self.gp, probe_idx, probe_t, m.t_surf[0].ravel()[probe_idx])
            loc = set(self.fdi.local_cells(probe_idx))
            local = [(c, float(t)) for c, t in zip(probe_idx, probe_t) if c in loc]
            probe_idx = [c for c, u in zip(probe_idx, used) if u]
            probe_t = probe_t[np.array(used, dtype=bool)]
        if probe_idx and self.use_gp:
            if probe_idx != self.gp.idx:
                self.gp.set_sensors(probe_idx)
            ts = m.t_surf[0].ravel()
            resid = probe_t - ts[probe_idx]
            delta = self.gp.correct(resid)
            # 표면만 보정 (코어는 물리식 R_in 을 통해 따라감).
            # 코어까지 같이 올리면 모델 냉각 편향이 있을 때 코어에 열이 계속 쌓이는 문제가 생김
            m.t_surf[0] += 0.6 * delta.reshape(tm.N_ROWS, tm.N_COLS)
            m.t_core[0] += self.core_gain * delta.reshape(tm.N_ROWS, tm.N_COLS)
            if s.get("t_coolant_in") is None:          # 냉각수 온도를 모르면 경계온도를 학습 (적분 동작)
                self.t_boundary += 0.3 * float(np.mean(resid))
            self.last_residual = resid
        elif not probe_idx:
            self.gp.set_sensors([])
        # 주변보다 뜨겁게 읽는 센서: 이웃 보정에는 안 쓰고, 그 셀에만 실측 반영 (국부 과열일 수 있으므로 버리지 않음)
        for cell, t_meas in local:
            r_, c_ = divmod(cell, tm.N_COLS)
            d = t_meas - float(m.t_surf[0, r_, c_])
            m.t_surf[0, r_, c_] += 0.6 * d
            m.t_core[0, r_, c_] += self.core_gain * d

        # ④ PINN 코어온도
        q = m.q_cell[0].ravel()
        c_rate = abs(current) / tm.CELL_CAPACITY_AH
        self.core_pinn = self.pinn.predict(m.t_surf[0].ravel(), q, soc, c_rate, max(dt, 1e-3))

        # ⑤ SOH: 측정 저항에서 현재 온도·SOC 의 기본 저항을 빼 노화분만 남김 (조건 만족 시에만 천천히 갱신)
        gap = float(np.abs(np.asarray(self.core_pinn) - m.t_core[0].ravel()).max())
        self.soh.update(self.rls.last_r_meas, m.t_core[0].ravel(), soc, gap, float(np.mean(self.gp.post_std)))
        self.samples += 1
        return self.outputs()

    def outputs(self) -> dict:
        m = self.model
        return {
            "surf": m.t_surf[0].ravel().copy(),
            "core_phys": m.t_core[0].ravel().copy(),
            "core_pinn": np.asarray(self.core_pinn).copy(),
            "post_std": self.gp.post_std.copy(),
            "q": m.q_cell[0].ravel().copy(),
            "k_cells": self.rls.k_cells.copy(),
            "soh": (soh := self.soh.estimate()),
            "soh_est": soh["soh"],                     # None = 아직 추정 중
            "soh_raw": self.rls.soh_estimate(),        # v0.4 이전 방식 (비교용)
            "t_boundary": float(self.t_boundary),
            "residual_rmse": float(np.sqrt(np.mean(self.last_residual**2))) if len(self.last_residual) else None,
            "faults": self.fdi.summary(self.probe_idx_all) if self.fdi_enabled else
            {"bad_sensors": [], "bad_reasons": {}, "bad_sensors_ever": [], "sensor_z": {}, "n_used": len(self.probe_idx_all), "events": []},
        }

    def forecast(self, current_profile, dt: float = 10.0, t_in: float | None = None) -> np.ndarray:
        """현재 상태에서 앞으로의 전류 계획대로 갔을 때 최고 코어온도의 시계열 예측 (PINN 보정 물리 모델)"""
        if self.model is None:
            return np.zeros(0)
        f = tm.PackThermalModel(1, 25.0, float(self.model.soc[0]))
        f.t_core = self.model.t_core.copy()
        f.t_surf = self.model.t_surf.copy()
        f.r_in, f.c_core = self.model.r_in.copy(), self.model.c_core.copy()
        r_abs = self.model.r_abs.copy() if self.model.r_abs is not None else None
        # 코어는 PINN 값에서 시작 (물리 코어 상태보다 정확)
        f.t_core[0] = np.asarray(self.core_pinn).reshape(tm.N_ROWS, tm.N_COLS)
        t_in = self.t_boundary if t_in is None else t_in
        out = []
        for i_now in current_profile:
            if r_abs is not None:
                f.r_abs = r_abs
            f.step(dt, np.array([i_now]), np.array([t_in]), self.cool, t_coolant_in_meas=np.array([t_in]))
            out.append(float(f.t_core.max()))
        return np.array(out)
