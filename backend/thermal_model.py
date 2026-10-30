"""
ThermalTwin 물리 모델 — 96셀 배터리팩 + 서펜타인 수냉판

[모델 구조] (참고 논문은 frontend '모델·데이터 출처' 탭과 README 참고)
1) 셀 열모델: 셀마다 2개 노드(코어 T_core, 표면 T_surf)를 갖는 집중정수 R-C 열회로
     C_core dT_core/dt = q - (T_core - T_surf)/R_in
     C_surf dT_surf/dt = (T_core - T_surf)/R_in - Q_coolant - Q_ambient + Q_lateral
   → Wang et al.(2023) BINN 논문의 열모델(Forgez 모델)과 같은 구조
2) 발열 모델(Bernardi 식): q = I^2 R0 - I T dU/dT   (I > 0 : 방전)
   - R0(T, SOC, 노화): Arrhenius 온도 의존 + SOC 의존 + 노화항 B1(e^(B2*δ)-1)
     (노화항 형태는 BINN 논문 식 (19)~(20))
   - dU/dT(SOC): Shen et al.(2025) Fig.8 엔트로피 계수 곡선 형태를 근사
3) 냉각판: 모듈(행)마다 서펜타인 채널 1개, 냉각수가 열 방향(col 0 → 11)으로 흐르며 데워짐
   - 레이놀즈수, 마찰계수(Shah & London), 발달 영역 Nusselt(Hausen) → 대류 열전달계수 h
   - 압력강하 Δp → 펌핑 동력 PP = Δp·Q  (Jiang et al.(2024) 식 (5))
4) 냉각 루프 + 칠러/히터: 냉각수 입구온도를 목표값으로 유지 (칠러 최대 8 kW, 히터 최대 5 kW)

모든 배열은 (B, 8, 12) 형태입니다. B = 동시에 계산하는 케이스 수
(실시간: 실제 차량 1개 + 디지털 트윈 1개 / 설계 탐색: 수백 개 케이스를 한 번에)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ─────────────────────────────────────────────
# 팩 구성: 8모듈 × 12셀 = 96S (Shen et al. 2025 Table 1 의 156 Ah 각형 셀 기준)
# ─────────────────────────────────────────────
N_ROWS, N_COLS = 8, 12
N_CELLS = N_ROWS * N_COLS

CELL_CAPACITY_AH = 156.0          # 정격 용량 [Ah]
CELL_MASS_KG = 2.75               # 셀 무게 [kg]
CELL_CP = 980.0                   # 비열 [J/(kg·K)] (Shen Table 4: 979.6)
C_CORE = 0.85 * CELL_MASS_KG * CELL_CP          # 코어 열용량 [J/K]
C_SURF = 0.15 * CELL_MASS_KG * CELL_CP + 450.0  # 케이스 + 냉각판 몫 [J/K]

R_IN = 0.12        # 코어→표면 열저항 [K/W] (대형 각형 셀)
R_TIM = 0.0693     # 갭필러 2 mm, k=2.5 W/mK, 셀 바닥 148×78 mm [K/W]
R_LAT = 4.0        # 이웃 셀 사이 2 mm 단열패드 [K/W]
R_AMB = 6.7        # 셀 상부 자연대류 (h≈5 W/m²K) [K/W]

# 전기적 특성
R0_REF = 0.35e-3   # 25 °C, SOC 50 % 에서 셀 내부저항 [Ω]
EA_OVER_R = 2400.0 # Arrhenius 계수 Ea/R [K]
AGING_B1 = 0.05e-3 # 노화 저항 계수 B1 [Ω]
AGING_B2 = 8.0     # 노화 저항 지수 B2 [-]

# NCM 셀 OCV(SOC) 표
_SOC_PTS = np.array([0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
_OCV_PTS = np.array([3.00, 3.45, 3.55, 3.62, 3.67, 3.72, 3.78, 3.86, 3.94, 4.02, 4.10, 4.18])
# 엔트로피 계수 dU/dT(SOC) [V/K] — Shen et al. 2025 Fig.8 곡선 형태 근사
_DUDT_SOC = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0])
_DUDT_PTS = np.array([-0.35, -0.10, -0.03, 0.07, 0.10, 0.15, 0.14, 0.03, 0.04, 0.05, 0.08]) * 1e-3

# 냉각수: 물-에틸렌글리콜 50/50 (25 °C 물성)
RHO_C = 1070.0     # 밀도 [kg/m³]
CP_C = 3300.0      # 비열 [J/(kg·K)]
K_C = 0.40         # 열전도도 [W/(m·K)]
MU_C = 3.4e-3      # 점도 [Pa·s]
PR_C = MU_C * CP_C / K_C

C_LOOP = 8.0 * RHO_C / 1000 * CP_C  # 냉각 루프 8 L 의 열용량 [J/K]
CHILLER_MAX_W = 8000.0              # 칠러 최대 냉각 용량 [W]
HEATER_MAX_W = 5000.0               # 냉각수 히터(PTC) 최대 가열 용량 [W] — 겨울철 배터리 예열
PUMP_EFFICIENCY = 0.35              # (v0.4 이전) 고정 펌프 효율 — 지금은 pump.py 의 성능곡선·효율 지도 사용
K_PORT = 1.5                        # 채널 입구(급축소 ≈0.5) + 출구(급확대 ≈1.0) 국부 손실계수
LOSS_SCALE = {"friction": 1.0, "bend": 1.0, "port": 1.0}   # 압력손실 모델 민감도 분석용 배율 (평소 1)

# 냉각판 형상 (셀 1개 아래에서 채널이 셀 폭 148 mm 를 가로질러 여러 번 왕복)
CELL_PITCH_ALONG_FLOW = 0.078  # 흐름 방향 셀 두께 [m]
CELL_SPAN = 0.148              # 채널이 가로지르는 셀 폭 [m]
CHANNEL_WALL = 0.003           # 채널 사이 벽 두께 [m]
BEND_RADIUS = 0.008            # 굽힘 반경 [m]
N_PARALLEL = 3                 # 모듈 냉각판 하나에 병렬 서펜타인 채널 3개 (각각 셀 폭의 1/3 담당)

# 모듈별 유량 분배 (U자형 매니폴드: 입구에서 먼 모듈일수록 유량이 적음)
FLOW_SHARE = np.array([1.12, 1.08, 1.04, 1.00, 0.97, 0.95, 0.93, 0.91])
FLOW_SHARE = FLOW_SHARE / FLOW_SHARE.mean()


def ocv(soc: np.ndarray) -> np.ndarray:
    return np.interp(soc, _SOC_PTS, _OCV_PTS)


def dudt(soc: np.ndarray) -> np.ndarray:
    return np.interp(soc, _DUDT_SOC, _DUDT_PTS)


def r_base(t_core_c: np.ndarray, soc: np.ndarray) -> np.ndarray:
    """노화를 제외한 내부저항 [Ω]: 저온·저SOC·고SOC 에서 커짐."""
    t_k = t_core_c + 273.15
    arrhenius = np.exp(EA_OVER_R * (1.0 / t_k - 1.0 / 298.15))
    soc_factor = 1.0 + 0.6 * np.exp(-soc / 0.08) + 0.3 * np.clip((soc - 0.85) / 0.15, 0.0, 1.0)
    return R0_REF * arrhenius * soc_factor


def r_aging(delta_decay: np.ndarray) -> np.ndarray:
    """노화로 늘어난 저항 [Ω]: B1·(e^(B2·δ) - 1), δ = 1 - SOH (BINN 논문 식 19~20 형태)."""
    return AGING_B1 * (np.exp(AGING_B2 * delta_decay) - 1.0)


# ─────────────────────────────────────────────
# 냉각판 유동/열전달 계산
# ─────────────────────────────────────────────
@dataclass
class CoolingResult:
    velocity: np.ndarray        # 채널 유속 [m/s]            (B, 8)
    reynolds: np.ndarray        # 레이놀즈수 [-]             (B, 8)
    h: np.ndarray               # 대류 열전달계수 [W/m²K]     (B, 8)
    ua_cell: np.ndarray         # 셀 1개 ↔ 냉각수 UA [W/K]   (B, 8)
    g_row: np.ndarray           # 모듈 채널의 ṁ·cp [W/K]     (B, 8)
    dp_pa: np.ndarray           # 냉각판 압력강하 [Pa]        (B,)
    pumping_w: np.ndarray       # 냉각판 유체 동력 PP [W]      (B,)
    pump_electric_w: np.ndarray # 펌프 전기 소비 [W] (냉각판 + 루프, 펌프 효율 지도 반영)  (B,)
    dp_friction_pa: np.ndarray | None = None   # 압력강하 내역: 직관 마찰
    dp_bend_pa: np.ndarray | None = None       #               180° 굽힘
    dp_port_pa: np.ndarray | None = None       #               입구·출구(매니폴드)
    pump: dict | None = None                   # pump.operating_point 결과 (회전수·효율·운전 가능 여부)


def cooling_plate(width_mm, depth_mm, flow_lpm, flow_share: np.ndarray | None = None) -> CoolingResult:
    """채널 폭/깊이/총유량으로 열전달계수와 펌핑 동력을 계산합니다. 입력은 (B,) 배열.
    flow_share: (B, 8) 모듈별 유량 분배 (평균 1). None 이면 설계값(U자형 매니폴드)"""
    w = np.asarray(width_mm, dtype=float) / 1000.0
    d = np.asarray(depth_mm, dtype=float) / 1000.0
    q_total = np.asarray(flow_lpm, dtype=float) / 60000.0  # [m³/s]

    area = w * d
    d_h = 2.0 * w * d / (w + d)                          # 수력직경
    aspect = np.minimum(w, d) / np.maximum(w, d)

    # 셀 하나 아래 채널 길이: 피치(w+벽)마다 (셀 폭 / 병렬 채널 수)를 한 번 가로지름
    n_pass = np.floor(CELL_PITCH_ALONG_FLOW / (w + CHANNEL_WALL)) + 1.0
    seg_len = CELL_SPAN / N_PARALLEL
    len_cell = n_pass * seg_len                 # 채널 1개 기준 셀 하나 아래 길이
    len_total = len_cell * N_COLS
    n_bends = n_pass * N_COLS

    share = FLOW_SHARE[None, :] if flow_share is None else np.asarray(flow_share, dtype=float)
    q_row = q_total[:, None] * share / N_ROWS   # (B, 8) 모듈별 유량
    v = q_row / N_PARALLEL / area[:, None]
    re = RHO_C * v * d_h[:, None] / MU_C

    # Darcy 마찰계수: 층류 Shah & London 직사각형 식 / 난류 Blasius
    a = aspect
    f_re = 96.0 * (1 - 1.3553 * a + 1.9467 * a**2 - 1.7012 * a**3 + 0.9564 * a**4 - 0.2537 * a**5)
    f_lam = f_re[:, None] / np.maximum(re, 1.0)
    f_turb = 0.316 * np.maximum(re, 1.0) ** -0.25
    f = np.where(re < 2300, f_lam, f_turb)
    k_bend = 0.4 + 0.8 * (w / (2 * BEND_RADIUS))          # 180° 굽힘 손실계수
    dyn = RHO_C * v**2 / 2.0
    wsum = np.sum(share, axis=1)
    dp_fric = np.sum(f * len_total[:, None] / d_h[:, None] * dyn * share, axis=1) / wsum
    dp_bend = np.sum(k_bend[:, None] * n_bends[:, None] * dyn * share, axis=1) / wsum
    dp_port = np.sum(K_PORT * dyn * share, axis=1) / wsum   # 매니폴드 → 채널 입구(급축소)·출구(급확대)
    dp_fric, dp_bend, dp_port = (dp_fric * LOSS_SCALE["friction"], dp_bend * LOSS_SCALE["bend"],
                                 dp_port * LOSS_SCALE["port"])
    dp = dp_fric + dp_bend + dp_port

    # Nusselt: 완전발달 층류(Shah & London) + 굽힘마다 경계층이 재발달하는 효과(Hausen)
    nu_fd = 7.541 * (1 - 2.610 * a + 4.970 * a**2 - 5.119 * a**3 + 2.702 * a**4 - 0.548 * a**5)
    gz = d_h[:, None] * re * PR_C / seg_len
    nu_lam = nu_fd[:, None] + 0.065 * gz / (1 + 0.04 * gz ** (2 / 3))
    f_g = (0.79 * np.log(np.maximum(re, 2300)) - 1.64) ** -2
    nu_turb = (f_g / 8) * (re - 1000) * PR_C / (1 + 12.7 * np.sqrt(f_g / 8) * (PR_C ** (2 / 3) - 1))
    nu = np.where(re < 2300, nu_lam, np.maximum(nu_turb, nu_lam))
    h = nu * K_C / d_h[:, None]

    wetted = (w + 2 * d) * len_cell * N_PARALLEL          # 셀 하나당 젖음 면적 [m²]
    r_conv = 1.0 / (h * wetted[:, None])
    ua_cell = 1.0 / (R_TIM + r_conv)
    g_row = q_row * RHO_C * CP_C

    pumping = dp * q_total
    from pump import operating_point  # noqa: PLC0415  (순환 import 방지)
    op = operating_point(np.asarray(flow_lpm, dtype=float), dp)
    return CoolingResult(v, re, h, ua_cell, g_row, dp, pumping, op["electric_w"], dp_fric, dp_bend, dp_port, op)


# ─────────────────────────────────────────────
# 팩 열모델
# ─────────────────────────────────────────────
class PackThermalModel:
    """B개 케이스의 96셀 팩을 동시에 적분합니다."""

    def __init__(self, batch: int, t_init_c: np.ndarray | float, soc_init: np.ndarray | float,
                 capacity_ah: np.ndarray | float = CELL_CAPACITY_AH):
        shape = (batch, N_ROWS, N_COLS)
        t0 = np.broadcast_to(np.asarray(t_init_c, dtype=float).reshape(-1, 1, 1), shape)
        self.batch = batch
        self.t_core = t0.copy()
        self.t_surf = t0.copy()
        self.soc = np.broadcast_to(np.asarray(soc_init, dtype=float), (batch,)).copy()
        self.capacity_ah = np.broadcast_to(np.asarray(capacity_ah, dtype=float), (batch,)).copy()
        self.t_coolant_in = np.broadcast_to(np.asarray(t_init_c, dtype=float).reshape(-1), (batch,)).copy()

        # 저항 계수: 실제 차량은 셀마다 편차, 트윈은 1.0
        self.r_scale = np.ones(shape)
        self.delta_decay = np.zeros(batch)   # δ = 1 - SOH
        self.r_abs: np.ndarray | None = None  # 셀별 저항을 직접 지정 (트윈: 셀 전압으로 식별한 값)

        # 셀 내부 열전달 파라미터 (실제 차량은 데이터시트 값과 다를 수 있음)
        self.r_in = np.full((batch, 1, 1), R_IN)
        self.c_core = np.full((batch, 1, 1), C_CORE)
        self.c_surf = np.full((batch, 1, 1), C_SURF)
        self.cool_scale = np.ones((batch, 1))  # 냉각 성능 보정 계수 (실차 보정용)
        self.cell_cool_scale = np.ones(shape)  # 셀별 냉각 성능 (고장 주입용: 갭필러 들뜸·채널 막힘 등, 기본 1)

        # 마지막 스텝 결과 (대시보드 표시용)
        self.q_cell = np.zeros(shape)
        self.q_coolant_cell = np.zeros(shape)
        self.t_coolant_out = self.t_coolant_in.copy()
        self.chiller_w = np.zeros(batch)

    # 현재 셀 저항 [Ω]
    def resistance(self) -> np.ndarray:
        if self.r_abs is not None:
            return self.r_abs
        return self.r_scale * r_base(self.t_core, self.soc[:, None, None]) + \
            r_aging(self.delta_decay)[:, None, None]

    def heat_generation(self, current_a: np.ndarray) -> np.ndarray:
        """셀별 발열량 [W]. current_a > 0 이면 방전, < 0 이면 충전."""
        i = current_a[:, None, None]
        q_irr = i**2 * self.resistance()
        q_rev = -i * (self.t_core + 273.15) * dudt(self.soc)[:, None, None]
        return q_irr + q_rev

    def step(self, dt: float, current_a: np.ndarray, t_amb_c: np.ndarray, cool: CoolingResult,
             chiller_set_c: np.ndarray | None = None, t_coolant_in_meas: np.ndarray | None = None,
             update_soc: bool = True) -> None:
        """dt 초만큼 적분. 칠러 설정값을 주면 냉각 루프도 계산, 측정 입구온도를 주면 그 값을 사용."""
        if t_coolant_in_meas is not None:
            self.t_coolant_in = np.asarray(t_coolant_in_meas, dtype=float).copy()

        q = self.heat_generation(current_a)
        ts = self.t_surf

        # 냉각수: 각 모듈 채널을 따라 셀을 지나며 데워짐 (ε-NTU 방식)
        ua = cool.ua_cell * self.cool_scale                                 # (B, 8)
        t_c = np.repeat(self.t_coolant_in[:, None], N_ROWS, axis=1)  # (B, 8)
        q_cool = np.empty_like(ts)
        for j in range(N_COLS):
            eff = 1.0 - np.exp(-ua * self.cell_cool_scale[:, :, j] / cool.g_row)
            qj = cool.g_row * eff * (ts[:, :, j] - t_c)
            q_cool[:, :, j] = qj
            t_c = t_c + qj / cool.g_row
        q_abs_total = q_cool.sum(axis=(1, 2))
        self.t_coolant_out = (t_c * cool.g_row).sum(axis=1) / cool.g_row.sum(axis=1)

        # 이웃 셀 사이 전도 (같은 모듈 안에서만)
        lateral = np.zeros_like(ts)
        lateral[:, :, 1:] += (ts[:, :, :-1] - ts[:, :, 1:]) / R_LAT
        lateral[:, :, :-1] += (ts[:, :, 1:] - ts[:, :, :-1]) / R_LAT

        # 외곽 셀은 외부로 열이 더 잘 빠짐
        r_amb = np.full((N_ROWS, N_COLS), R_AMB)
        r_amb[0, :] *= 0.6
        r_amb[-1, :] *= 0.6
        r_amb[:, 0] *= 0.6
        r_amb[:, -1] *= 0.6
        q_amb = (ts - t_amb_c[:, None, None]) / r_amb

        q_core_to_surf = (self.t_core - ts) / self.r_in
        self.t_core = self.t_core + dt * (q - q_core_to_surf) / self.c_core
        self.t_surf = ts + dt * (q_core_to_surf - q_cool - q_amb + lateral) / self.c_surf

        # 냉각 루프 + 칠러(+) / 히터(-)
        if chiller_set_c is not None:
            g_tot = cool.g_row.sum(axis=1)
            demand = g_tot * (self.t_coolant_out - chiller_set_c)
            self.chiller_w = np.clip(demand, -HEATER_MAX_W, CHILLER_MAX_W)
            self.t_coolant_in = self.t_coolant_in + dt * (q_abs_total - self.chiller_w) / C_LOOP

        if update_soc:
            self.soc = np.clip(self.soc - current_a * dt / 3600.0 / self.capacity_ah, 0.0, 1.0)

        self.q_cell = q
        self.q_coolant_cell = q_cool


    def cell_voltages(self, current_a: np.ndarray) -> np.ndarray:
        """셀별 단자전압 [V] = OCV − I·R (모든 셀 전압은 실제 BMS 가 측정하는 값)"""
        return ocv(self.soc)[:, None, None] - current_a[:, None, None] * self.resistance()


def pack_ocv(soc: np.ndarray) -> np.ndarray:
    return N_CELLS * ocv(soc)


def current_for_power(power_w: np.ndarray, soc: np.ndarray) -> np.ndarray:
    """요청 출력(+방전 / -충전)에 필요한 팩 전류 [A] (OCV 기준 근사)."""
    return power_w / pack_ocv(soc)


def fast_charge_limit_w(soc: np.ndarray) -> np.ndarray:
    """일반적인 DC 급속충전 곡선: SOC 50 % 이후 출력을 줄임 (최대 출력 대비 비율)."""
    return np.interp(soc, [0.0, 0.5, 0.8, 1.0], [1.0, 1.0, 0.45, 0.1])


def thermal_derate(t_ctrl_c: np.ndarray, start_c: float = 45.0, end_c: float = 55.0) -> np.ndarray:
    """BMS 열 디레이팅: start_c 부터 출력을 줄여 end_c 에서 20 %."""
    return np.interp(t_ctrl_c, [start_c, end_c], [1.0, 0.2])
