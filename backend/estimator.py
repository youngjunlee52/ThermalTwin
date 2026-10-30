"""
추정기 모음 — 센서 수·위치가 바뀌어도 동작하도록 설계

1) CellResistanceRLS  : 96셀 '전압'으로 셀마다 내부저항을 식별 (RLS)
   - 실제 BMS 는 셀 전압을 96개 모두 측정합니다 (온도센서는 일부만). 300대 EV 공개 데이터도 동일.
   - OCV 표가 없어도 되도록 (셀 전압 − 팩 평균)과 전류 변화량(ΔV/ΔI)만 사용
   → 온도센서가 없는 셀의 발열도 알 수 있으므로 '센서 사이에 숨은 약한 셀' 문제를 줄입니다.

2) SpatialGP          : 학습된 공간 공분산(가우시안 프로세스 / 크리깅)으로 센서 오차를 96셀 전체에 분배
   - 어떤 센서 조합이 와도 칼만 이득처럼 최적 가중치를 다시 계산 → 센서 수/위치 변경에 자동 대응
   - 사후 분산 = 셀마다 '얼마나 모르는지'(불확실성 지도)
   - 같은 공분산으로 최적 센서 배치를 탐욕 알고리즘으로 계산 (Krause et al. 2008 방식)
"""
from __future__ import annotations


import numpy as np

import thermal_model as tm

from paths import MODEL_DIR  # noqa: E402
N = tm.N_CELLS


def cell_index(r: int, c: int) -> int:
    return r * tm.N_COLS + c


def index_cell(i: int) -> list[int]:
    return [int(i // tm.N_COLS), int(i % tm.N_COLS)]


# ─────────────────────────────────────────────
# 1) 셀 전압 기반 저항 식별
# ─────────────────────────────────────────────
class CellResistanceRLS:
    """R_i = k_i · r_base(T_i, SOC).  k̄(팩 평균)는 ΔV̄/ΔI 로, k_i − k̄ 는 (V_i − V̄) 와 전류의 관계로 추정."""

    def __init__(self, forget: float = 0.998, k_prior: float = 1.0, ocv_known: bool = True):
        self.ocv_known = ocv_known      # OCV 표를 아는 경우(제조사 BMS·시뮬레이션). 공개 실차 데이터는 비공개라 False
        self.k_mean = k_prior
        self.p_mean = 0.5
        self.dk = np.zeros(N)                 # 셀별 k_i − k̄
        self.a = np.zeros(N)                  # 셀별 OCV 오프셋 (SOC 불균형)
        self.P = np.tile(np.diag([1e-4, 0.3]), (N, 1, 1))  # [a, dk] 공분산
        self.forget = forget
        self.prev_v_mean = None
        self.prev_i = None
        self.prev_ocv_slope_soc = None
        self.prev_cell_dev = None
        self.prev_cell_i = 0.0
        self.samples = 0
        self.last_r_meas: float | None = None   # 이번 샘플에서 측정한 팩 평균 셀 저항 [Ω] (SOH 추정기로 넘김)

    def update(self, v_cells: np.ndarray, current: float, rb: np.ndarray, soc: float) -> None:
        """v_cells (96,), rb = r_base(T_i, SOC) (96,)"""
        v = np.asarray(v_cells, dtype=float)
        v_mean = float(v.mean())
        rb_mean = float(rb.mean())
        self.last_r_meas = None
        # (a-1) OCV 를 알면: 정전류 충전 중에도 V̄ − OCV(SOC) = −I·k̄·r̄ 로 바로 추정
        if self.ocv_known and abs(current) > 30.0:
            k_meas = -(v_mean - float(tm.ocv(np.array([soc]))[0])) / (current * rb_mean)
            if 0.3 < k_meas < 6.0:
                self.k_mean += 0.08 * (k_meas - self.k_mean)
                self.last_r_meas = k_meas * rb_mean
        # (a-2) OCV 없이: 전류가 바뀐 순간의 ΔV̄/ΔI
        if self.prev_i is not None:
            d_i = current - self.prev_i
            if abs(d_i) > 8.0:
                d_ocv = float(tm.ocv(np.array([soc]))[0]) - self.prev_ocv_slope_soc
                y = (v_mean - self.prev_v_mean) - d_ocv
                phi = -d_i * rb_mean
                k_meas = y / phi
                if 0.3 < k_meas < 5.0:
                    if not self.ocv_known:
                        self.last_r_meas = k_meas * rb_mean
                    gain = self.p_mean / (self.p_mean + 0.05)
                    self.k_mean += gain * (k_meas - self.k_mean)
                    self.p_mean = (1 - gain) * self.p_mean / 0.99 + 1e-4
        self.prev_i, self.prev_v_mean = current, v_mean
        self.prev_ocv_slope_soc = float(tm.ocv(np.array([soc]))[0])

        # (b) 셀별 편차 dk_i
        if self.ocv_known:
            # OCV 를 알면 절대식: y_i = V_i − V̄ = a_i − I·rb_i·dk_i (RLS)
            y = v - v_mean
            phi = np.stack([np.ones(N), -current * rb], axis=1)        # (96, 2)
            self._rls(phi, y)
        elif self.prev_cell_dev is not None and abs(current - self.prev_cell_i) > 5.0:
            # OCV 없이(실차): 전류가 바뀐 순간의 Δ(V_i − V̄) = −ΔI·rb_i·dk_i → SOC 불균형 오프셋과 섞이지 않음
            y = (v - v_mean) - self.prev_cell_dev
            phi = np.stack([np.zeros(N), -(current - self.prev_cell_i) * rb], axis=1)
            self._rls(phi, y)
        self.prev_cell_dev, self.prev_cell_i = v - v_mean, current
        self.samples += 1

    def _rls(self, phi: np.ndarray, y: np.ndarray, sigma_v: float = 0.003) -> None:
        phi, y = phi / sigma_v, y / sigma_v          # 전압 잡음(≈3 mV) 기준으로 정규화
        Pphi = np.einsum("nij,nj->ni", self.P, phi)
        denom = self.forget + np.einsum("ni,ni->n", phi, Pphi) + 1e-12
        gain = Pphi / denom[:, None]
        theta = np.stack([self.a, self.dk], axis=1)
        err = y - np.einsum("ni,ni->n", phi, theta)
        theta = theta + gain * err[:, None]
        self.P = (self.P - np.einsum("ni,nj->nij", gain, Pphi)) / self.forget
        self.P[:, 1, 1] = np.minimum(self.P[:, 1, 1], 0.3)
        self.a, self.dk = theta[:, 0], np.clip(theta[:, 1], -0.6, 1.5)

    @property
    def k_cells(self) -> np.ndarray:
        return np.clip(self.k_mean + self.dk, 0.3, 5.0)

    def soh_estimate(self, rb_mean_ref: float = tm.R0_REF) -> float:
        """[v0.4 이전 방식 — 비교용] 노화 저항 ≈ (k̄ − 1)·r_base(25 °C).
        k̄ 는 현재 온도의 r_base 기준 배율이라, 뜨거울수록(r_base 작음) 노화를 과대평가하는 문제가 있었음"""
        r_aging = max(0.0, (self.k_mean - 1.0) * rb_mean_ref)
        return soh_from_r_aging(r_aging)


def soh_from_r_aging(r_aging: float) -> float:
    """BINN 노화 저항식 R_aging = B1·(e^(B2·δ) − 1) 을 거꾸로: δ = ln(1 + R_aging/B1)/B2, SOH = 1 − δ"""
    delta = np.log(1.0 + max(0.0, r_aging) / tm.AGING_B1) / tm.AGING_B2
    return float(np.clip(1.0 - delta, 0.5, 1.0))


# ─────────────────────────────────────────────
# 1-b) SOH 추정기: 외부 환경과 노화를 분리
# ─────────────────────────────────────────────
class SOHEstimator:
    """셀 저항 = R0(T, SOC) + R_aging(SOH)  (물리 모델과 같은 BINN 구조) 를 그대로 거꾸로 풉니다.

      R_aging 측정값 = R_측정(셀 전압·전류) − R0(트윈이 추정한 코어온도, SOC)
    → 온도·SOC 영향은 R0 쪽에서 빠지므로 외기·냉각수 유량·전류가 바뀌어도 SOH 가 흔들리지 않아야 함.

    외부 환경과 혼동되지 않게:
      · 갱신 조건(게이트): 전류가 충분하고(신호/잡음), 코어 10–45 °C (모델 검증 범위), SOC 15–90 %,
        PINN 신경망 ↔ 하이브리드 코어 차이 < 2 K (코어온도 추정을 믿을 수 있을 때만)
      · 느린 갱신: 망각계수 0.998 의 가중 평균 (유효 표본 약 500개). SOH 는 몇 달에 걸쳐 변하는 값
      · 신뢰구간: 표본 분산(자기상관 보정 ×3) + 코어온도 불확실성이 R0 를 통해 들어오는 계통 오차
    """
    FORGET = 0.998
    T_RANGE = (10.0, 45.0)
    SOC_RANGE = (0.15, 0.90)
    GAP_MAX_K = 2.0
    MIN_EFF_N = 20            # 이 만큼 쌓이기 전에는 '추정 중'
    AUTOCORR = 3.0

    def __init__(self):
        self.w = 0.0
        self.mean = 0.0
        self.m2 = 0.0
        self.n_used = 0
        self.n_skipped = 0
        self.last_gate: list[str] = []
        self.t_sigma = 0.5

    def update(self, r_meas: float | None, t_core: np.ndarray, soc: float, model_gap_k: float, post_std_k: float) -> None:
        if r_meas is None:              # 이번 샘플엔 저항 측정이 없음 (보류로 세지 않음)
            self.last_gate = ["전류가 작거나 변화가 없어 저항을 측정할 수 없음"]
            return
        t_mean = float(np.mean(t_core))
        gate = []
        if not self.T_RANGE[0] <= t_mean <= self.T_RANGE[1]:
            gate.append(f"코어 평균 {t_mean:.0f} °C (갱신 범위 {self.T_RANGE[0]:.0f}–{self.T_RANGE[1]:.0f} °C 밖)")
        if not self.SOC_RANGE[0] <= soc <= self.SOC_RANGE[1]:
            gate.append(f"SOC {soc * 100:.0f} % (갱신 범위 {self.SOC_RANGE[0] * 100:.0f}–{self.SOC_RANGE[1] * 100:.0f} % 밖)")
        if model_gap_k >= self.GAP_MAX_K:
            gate.append(f"코어온도 추정 불확실 (모델 차이 {model_gap_k:.1f} K)")
        self.last_gate = gate
        if gate:
            self.n_skipped += 1
            return
        r0 = float(np.mean(tm.r_base(np.asarray(t_core), np.array(soc))))
        x = r_meas - r0                 # 노화 저항 측정값 [Ω]
        self.w = self.FORGET * self.w + 1.0
        d = x - self.mean
        self.mean += d / self.w
        self.m2 = self.FORGET * self.m2 + d * (x - self.mean)
        self.n_used += 1
        # 코어온도 불확실성(σ_T): GP 사후 표준편차와 모델 차이의 절반 중 큰 쪽, 최소 0.5 K
        self.t_sigma = max(0.5, post_std_k, model_gap_k / 2)
        self._r0_at = (r0, t_mean)

    def estimate(self) -> dict:
        if self.w < self.MIN_EFF_N:
            return {"soh": None, "lo": None, "hi": None, "n_used": self.n_used, "n_skipped": self.n_skipped,
                    "status": "추정 중 — 갱신 조건을 만족하는 데이터가 더 필요" + (f" ({self.last_gate[0]})" if self.last_gate else "")}
        var = max(self.m2 / self.w, 0.0)
        se_stat = np.sqrt(var / self.w * self.AUTOCORR)
        r0, t_mean = self._r0_at
        se_sys = r0 * tm.EA_OVER_R / (t_mean + 273.15) ** 2 * self.t_sigma   # dR0/dT · σ_T
        se = float(np.hypot(se_stat, se_sys))
        soh = soh_from_r_aging(self.mean)
        lo, hi = soh_from_r_aging(self.mean + 1.96 * se), soh_from_r_aging(self.mean - 1.96 * se)
        status = "갱신 중" if not self.last_gate else f"갱신 보류: {self.last_gate[0]}"
        return {"soh": soh, "lo": lo, "hi": hi, "n_used": self.n_used, "n_skipped": self.n_skipped, "status": status}


# ─────────────────────────────────────────────
# 2) 학습된 공간 공분산 기반 보정 (임의 센서 배치)
# ─────────────────────────────────────────────
class SpatialGP:
    def __init__(self, cov: np.ndarray | None = None, noise_k: float = 0.3):
        if cov is None:
            cov = load_spatial_cov()
        self.cov = cov                      # (96, 96) 표면 오차 공분산 (단위 분산으로 정규화)
        self.noise = noise_k
        self.scale2 = 1.0                   # 실시간으로 추정하는 오차 크기 (K²)
        self.resid_var = 1.0
        self.set_sensors([])

    def set_sensors(self, idx: list[int]) -> None:
        self.idx = list(dict.fromkeys(int(i) for i in idx))
        self._refresh()

    def _refresh(self) -> None:
        if not self.idx:
            self.gain = np.zeros((N, 0))
            self.post_var = np.diag(self.cov).copy()
            return
        s = self.idx
        k_ss = self.cov[np.ix_(s, s)] * self.scale2 + (self.noise**2) * np.eye(len(s))
        k_xs = self.cov[:, s] * self.scale2
        sol = np.linalg.solve(k_ss, k_xs.T)                       # (n, 96)
        self.gain = sol.T                                          # (96, n)
        self.post_var = np.maximum(np.diag(self.cov) * self.scale2 - np.einsum("ij,ji->i", k_xs, sol), 0.0)

    def correct(self, residual: np.ndarray) -> np.ndarray:
        """센서 오차(측정 − 예측) → 96셀 표면 보정량 (사후 평균)"""
        if not self.idx:
            return np.zeros(N)
        r = np.asarray(residual, dtype=float)
        # 오차 크기를 실시간으로 갱신 (적응형 신호 분산)
        self.resid_var = 0.97 * self.resid_var + 0.03 * float(np.mean(r**2))
        base = float(np.mean(np.diag(self.cov)[self.idx]))
        new_scale = float(np.clip((self.resid_var - 0.5 * self.noise**2) / max(base, 1e-6), 0.05, 25.0))
        if abs(new_scale - self.scale2) / self.scale2 > 0.15:
            self.scale2 = new_scale
            self._refresh()
        return self.gain @ r

    @property
    def post_std(self) -> np.ndarray:
        return np.sqrt(self.post_var)

    def loo_z(self, all_idx: list[int], resid: np.ndarray, used: list[bool]) -> np.ndarray:
        """센서마다 '나머지 센서로 예측한 값'과 비교한 정규화 오차 z (leave-one-out).
        보정에 쓰는 센서는 K⁻¹ 공식으로, 빼 둔 센서는 쓰는 센서들로 예측. |z| 가 크면 그 센서만 혼자 튀는 것"""
        r = np.asarray(resid, dtype=float)
        s = [i for i, u in zip(all_idx, used) if u]
        rs = np.array([x for x, u in zip(r, used) if u])
        z = np.zeros(len(all_idx))
        if len(s) < 2:
            return z
        k_ss = self.cov[np.ix_(s, s)] * self.scale2 + (self.noise**2) * np.eye(len(s))
        k_inv = np.linalg.inv(k_ss)
        alpha = k_inv @ rs
        pos = {cell: n for n, cell in enumerate(s)}
        for n, (cell, u) in enumerate(zip(all_idx, used)):
            if u:
                p = pos[cell]
                z[n] = (alpha[p] / k_inv[p, p]) * np.sqrt(k_inv[p, p])
            else:
                k = self.cov[cell, s] * self.scale2
                var = self.cov[cell, cell] * self.scale2 - k @ k_inv @ k + self.noise**2
                z[n] = (r[n] - k @ alpha) / np.sqrt(max(var, 1e-6))
        return z


class SensorFaultMonitor:
    """온도센서 고장 진단 (FDI) — 안전 쪽으로 기운 비대칭 규칙
      나머지 센서로 예측한 값과의 차이 z (leave-one-out) 의 이동평균이 |z| > 4 를 3회 연속이면 이상.
      · 주변보다 '차갑게' 읽음 (치우침·서서히 어긋남) 또는 값 고착(모델은 그보다 뜨거움)
            → 열을 숨기는 방향이므로 '격리': 보정에서 뺌
      · 주변보다 '뜨겁게' 읽음
            → 센서 고장인지 진짜 국부 과열(예: 그 셀만 냉각 불량)인지 데이터로는 구분할 수 없음.
              안전을 위해 값을 버리지 않고 '국부 반영': 그 셀에만 실측을 반영하고 이웃 셀 보정에는 쓰지 않음 + 현장 확인 경고
            (단열 패드 때문에 셀 하나의 과열은 이웃 센서에 거의 안 보이므로, 혼자 뜨거운 센서는 실제 과열일 수 있음)
      z 가 다시 작아지면 30 샘플 뒤 정상 복귀. 보정에 쓰는 센서가 3개 미만이면 더 격리하지 않음."""
    Z_LIMIT = 4.0
    Z_READMIT = 2.0
    ENTER_N = 3
    READMIT_N = 30
    WARMUP = 30
    STUCK_N = 10

    def __init__(self):
        self.st: dict[int, dict] = {}
        self.samples = 0
        self.ever: set[int] = set()
        self.events: list[str] = []

    def _s(self, cell: int) -> dict:
        return self.st.setdefault(cell, {"ema": 0.0, "bad_n": 0, "ok_n": 0, "excluded": False, "local": False, "why": "",
                                         "last": None, "same_n": 0, "model_move": 0.0, "last_model": None, "q": None})

    def update(self, gp: "SpatialGP", idx: list[int], meas: np.ndarray, model_ts: np.ndarray) -> list[bool]:
        """이번 샘플에서 보정에 쓸 센서 표시(True=사용)를 돌려줌"""
        self.samples += 1
        self.events = []
        for cell, v, mt in zip(idx, meas, model_ts):          # 고착 판단용 기록
            s = self._s(cell)
            if s["last"] is not None:
                d = abs(v - s["last"])
                if d > 1e-6:
                    s["q"] = d if s["q"] is None else min(s["q"], d)   # 관측된 최소 변화 = 분해능 근사
                    s["same_n"], s["model_move"], s["model_at_freeze"] = 0, 0.0, mt
                else:
                    s["same_n"] += 1
                    # 같은 값이 시작된 뒤 모델 온도의 '순 변화' (오르내림 누적이 아님)
                    s["model_move"] = abs(mt - s.get("model_at_freeze", mt))
            else:
                s["model_at_freeze"] = mt
            s["last"], s["last_model"] = v, mt
        used = [not (self._s(c)["excluded"] or self._s(c)["local"]) for c in idx]
        if self.samples <= self.WARMUP or len(idx) < 3:
            return used
        z = gp.loo_z(idx, meas - model_ts, used)
        for n, cell in enumerate(idx):
            s = self._s(cell)
            s["ema"] = 0.8 * s["ema"] + 0.2 * float(z[n])
            stuck_limit = max(1.5, 3 * (s["q"] or 0.0))     # 분해능이 거친 센서(실차 1 °C)는 기준을 높임
            stuck = s["same_n"] >= self.STUCK_N and s["model_move"] >= stuck_limit
            stuck_cold = stuck and model_ts[n] > meas[n]       # 고착된 값이 모델보다 낮음 = 열을 숨김
            if not (s["excluded"] or s["local"]):
                s["bad_n"] = s["bad_n"] + 1 if (abs(s["ema"]) > self.Z_LIMIT or stuck) else 0
                if s["bad_n"] >= self.ENTER_N and sum(used) > 3:
                    s["ok_n"] = 0
                    used[n] = False
                    self.ever.add(cell)
                    if stuck_cold or (not stuck and s["ema"] < 0):
                        s["excluded"] = True
                        s["why"] = (f"값 고착 ({s['same_n']}회 같은 값, 모델은 {s['model_move']:.1f} K 더 뜨거움)" if stuck else
                                    f"주변보다 차갑게 읽음 z = {s['ema']:+.1f} — 열을 숨길 수 있어 격리")
                        self.events.append(f"센서 {index_cell(cell)} 격리: {s['why']}")
                    else:
                        s["local"] = True
                        s["why"] = (f"주변보다 뜨겁게 읽음 (z = {s['ema']:+.1f}) — 국부 과열 또는 센서 이상, 현장 확인 필요. "
                                    "안전을 위해 그 셀에는 실측을 반영")
                        self.events.append(f"센서 {index_cell(cell)} 국부 반영: {s['why']}")
            else:
                s["ok_n"] = s["ok_n"] + 1 if (abs(s["ema"]) < self.Z_READMIT and not stuck) else 0
                if s["ok_n"] >= self.READMIT_N:
                    s["excluded"] = s["local"] = False
                    s["bad_n"], s["why"] = 0, ""
                    used[n] = True
                    self.events.append(f"센서 {index_cell(cell)} 복귀: 다시 주변과 일치")
        return used

    def local_cells(self, idx: list[int]) -> list[int]:
        return [c for c in idx if self._s(c)["local"]]

    def summary(self, idx: list[int]) -> dict:
        bad = [c for c in idx if self._s(c)["excluded"]]
        local = [c for c in idx if self._s(c)["local"]]
        return {"bad_sensors": [index_cell(c) for c in bad],
                "local_sensors": [index_cell(c) for c in local],
                "bad_reasons": {f"{index_cell(c)[0]}-{index_cell(c)[1]}": self.st[c]["why"] for c in bad + local},
                "bad_sensors_ever": [index_cell(c) for c in sorted(self.ever)],
                "sensor_z": {f"{index_cell(c)[0]}-{index_cell(c)[1]}": round(self._s(c)["ema"], 2) for c in idx},
                "n_used": len(idx) - len(bad), "events": list(self.events)}


def probe_covariance(probe_series: list[np.ndarray], common_k2: float = 4.0, nugget: float = 0.05) -> np.ndarray:
    """실차 데이터에서 프로브 간 공분산 학습: (팩 평균 대비 편차)의 공분산 + 공통 성분.
    probe_series: [(T, P) 배열, ...] 여러 주행/충전 구간"""
    dev = np.concatenate([x - x.mean(axis=1, keepdims=True) for x in probe_series])
    return np.cov(dev.T) + common_k2 + nugget * np.eye(dev.shape[1])


def expand_probe_cov(sigma_p: np.ndarray, probe_cells: list[list[int]]) -> np.ndarray:
    """프로브 공분산(P×P) → 96셀 공분산: 각 셀을 같은 모듈의 가장 가까운 프로브에 연결"""
    owner = np.zeros(N, dtype=int)
    pc = np.array(probe_cells)
    for i in range(N):
        r, c = divmod(i, tm.N_COLS)
        d = np.abs(pc[:, 0] - r) * 100 + np.abs(pc[:, 1] - c)
        owner[i] = int(np.argmin(d))
    cov = sigma_p[np.ix_(owner, owner)].copy()
    cov[np.diag_indices(N)] += 0.02
    return cov


def load_spatial_cov() -> np.ndarray:
    f = MODEL_DIR / "spatial_cov.npz"
    if f.exists():
        return np.load(f)["cov"]
    return default_cov()


def default_cov() -> np.ndarray:
    """학습 파일이 없을 때 쓰는 기본 공분산: 같은 모듈·흐름 방향으로 상관이 큰 지수 커널"""
    rr, cc = np.divmod(np.arange(N), tm.N_COLS)
    d_row = np.abs(rr[:, None] - rr[None, :])
    d_col = np.abs(cc[:, None] - cc[None, :])
    return 0.5 * np.exp(-d_row / 1.5 - d_col / 6.0) + 0.5 * np.exp(-d_col / 4.0) * 0.3 + 0.05 * np.eye(N)


# ─────────────────────────────────────────────
# 센서 배치
# ─────────────────────────────────────────────
def expected_rmse(cov: np.ndarray, idx: list[int], noise: float = 0.3) -> float:
    gp = SpatialGP(cov, noise)
    gp.set_sensors(idx)
    return float(np.sqrt(np.mean(gp.post_var)))


def greedy_layout(cov: np.ndarray, n: int, noise: float = 0.3) -> list[int]:
    """사후 분산 합을 가장 많이 줄이는 셀을 하나씩 추가 (탐욕 알고리즘, 근사 최적 보장)"""
    chosen: list[int] = []
    for _ in range(n):
        best, best_val = None, np.inf
        for cand in range(N):
            if cand in chosen:
                continue
            gp_idx = chosen + [cand]
            k_ss = cov[np.ix_(gp_idx, gp_idx)] + noise**2 * np.eye(len(gp_idx))
            k_xs = cov[:, gp_idx]
            val = float(np.trace(cov) - np.einsum("ij,ji->", k_xs, np.linalg.solve(k_ss, k_xs.T)))
            if val < best_val:
                best, best_val = cand, val
        chosen.append(int(best))
    return chosen


def uniform_layout(n: int) -> list[int]:
    """모듈마다 고르게, 열 위치는 모듈마다 엇갈리게 배치하는 일반적인 방식"""
    n = max(1, min(N, n))
    per_row = [n // tm.N_ROWS + (1 if r < n % tm.N_ROWS else 0) for r in range(tm.N_ROWS)]
    if n < tm.N_ROWS:   # 센서가 모듈 수보다 적으면 모듈을 건너뛰며 배치
        rows = [int(round(x)) for x in np.linspace(0, tm.N_ROWS - 1, n)]
        per_row = [rows.count(r) for r in range(tm.N_ROWS)]
    out = []
    for r, k in enumerate(per_row):
        if k == 0:
            continue
        shift = 0.25 if r % 2 else -0.25
        cols = [int(round((j + 0.5 + (shift if k < tm.N_COLS else 0)) * tm.N_COLS / k - 0.5)) for j in range(k)]
        out += [cell_index(r, min(tm.N_COLS - 1, max(0, c))) for c in cols]
    return list(dict.fromkeys(out))


def random_layout(n: int, rng: np.random.Generator) -> list[int]:
    return [int(i) for i in rng.choice(N, size=n, replace=False)]
