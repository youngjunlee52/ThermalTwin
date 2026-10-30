"""
ThermalTwin 실시간 엔진 — 데이터 소스 3가지를 같은 디지털 트윈으로 처리하고 제어 명령을 냅니다.

  source = "sim"     시뮬레이션 차량 (진짜 값을 알아 검증 가능, 제어 명령이 차량에 바로 적용)
  source = "replay"  실차 로그 재생 (300대 EV 공개 데이터 등). 일부 프로브만 트윈에 주고 나머지로 검증
  source = "ingest"  외부 실시간 입력 (BMS·CAN 게이트웨이가 /ws/ingest 또는 POST /api/telemetry 로 전송)

매 샘플: 텔레메트리 → 트윈 추정(셀 저항 → 물리 → GP 보정 → PINN) → 위험 판정 → 제어 명령(충전 출력 상한·펌프 유량)
"""
from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import asdict, dataclass

import numpy as np

import estimator as est
import ingest_guard
import runaway
import thermal_model as tm
from pinn import PINNVirtualSensor
from sim_vehicle import SimVehicle
from store import LogStore
from paths import DATA_DIR, MODEL_DIR, UPLOAD_DIR, dataset_dirs  # noqa: F401  (main.py 가 여기서 가져감)
from twin import TwinEstimator

T_CORE_WARNING_C = 45.0
T_CORE_DANGER_C = 55.0
DELTA_T_WARNING_K = 8.0
MODEL_GAP_WARNING_K = 2.0
SIM_SAMPLE_DT = 2.0          # 시뮬레이션 차량 BMS 샘플 주기 [s]
FORECAST_MIN = 10

# ── 안전 모드(Fail-safe): 트윈(AI) 추정을 믿기 어려우면 규칙 기반 제어로 자동 전환 ──
FALLBACK_GAP_K = 3.0         # PINN 신경망 ↔ 하이브리드 코어 차이
FALLBACK_STD_K = 1.0         # 96셀 평균 추정 불확실성(σ)
FALLBACK_RESID_K = 3.0       # 센서 실측 ↔ 모델 예측 차이 (모델 발산)
FALLBACK_REJECT_N = 3        # 연속 입력 거부 횟수 (센서·통신 이상)
FALLBACK_ENTER_N = 3         # 위 조건이 연속 N 샘플이면 진입 (순간 잡음 무시)
FALLBACK_CLEAR_RATIO = 0.7   # 해제는 모든 지표가 한계의 70 % 아래일 때만 '정상'으로 셈 (한계 근처에서 깜빡임 방지)
FALLBACK_EXIT_S = 60.0       # '정상'이 이 시간(차량 시각) 이어져야 AI 제어로 복귀
FALLBACK_MARGIN_K = 5.0      # 규칙 기반: 센서 최고값 + 여유 (표면→코어 차이 3–5 K 를 덮는 값)
FALLBACK_FLOW_LPM = 20.0     # 규칙 기반: 냉각수 유량 하한
STALE_S = 5.0                # 외부 입력이 이보다 오래 끊기면 마지막 명령은 무효

# ── 입력 우선순위: 실측(외부 수신·실차 재생)이 들어오는 항목은 화면 슬라이더로 바꿀 수 없음 ──
#   (제어 정책 — 디레이팅·제어 기준·펌프·충전 출력 상한·냉각수 목표 — 는 항상 화면에서 조정 가능)
LOCKED_BY_SOURCE = {
    "sim": set(),
    "replay": {"mode", "ambient_c", "true_soh", "channel_width_mm", "channel_depth_mm"},
    "ingest": {"mode", "ambient_c", "true_soh", "time_scale", "channel_width_mm", "channel_depth_mm"},
}


@dataclass
class EngineControls:
    source: str = "sim"
    dataset: str = "ev_charge_strong"
    mode: str = "fast_charge"
    charge_power_kw: float = 150.0
    ambient_c: float = 25.0
    coolant_set_c: float = 20.0
    coolant_flow_lpm: float = 10.0
    channel_width_mm: float = 8.0
    channel_depth_mm: float = 3.0
    true_soh: float = 0.85
    derating: bool = True
    derate_source: str = "twin"          # sensor | twin | forecast
    auto_pump: bool = True
    use_voltage: bool = True
    time_scale: float = 30.0
    force_fallback: bool = False          # 사용자가 안전 모드(규칙 기반 제어)를 강제

    def update(self, msg: dict) -> set[str]:
        """화면 설정 반영. 현재 소스에서 실측으로 정해지는 항목(LOCKED_BY_SOURCE)은 무시"""
        changed = set()
        locked = LOCKED_BY_SOURCE.get(self.source, set())
        msg = {k: v for k, v in msg.items() if k not in locked}

        def num(key, lo, hi):
            if key in msg:
                v = float(min(hi, max(lo, float(msg[key]))))
                if v != getattr(self, key):
                    setattr(self, key, v)
                    changed.add(key)
        if msg.get("mode") in ("fast_charge", "highway", "city", "aggressive", "rest") and msg["mode"] != self.mode:
            self.mode = msg["mode"]
            changed.add("mode")
        for k, lo, hi in (("charge_power_kw", 20, 200), ("ambient_c", -20, 45), ("coolant_set_c", 10, 35),
                          ("coolant_flow_lpm", 1, 30), ("channel_width_mm", 4, 10), ("channel_depth_mm", 1, 4),
                          ("true_soh", 0.65, 1.0), ("time_scale", 1, 120)):
            num(k, lo, hi)
        for k in ("derating", "auto_pump", "use_voltage", "force_fallback"):
            if k in msg and bool(msg[k]) != getattr(self, k):
                setattr(self, k, bool(msg[k]))
                changed.add(k)
        if msg.get("derate_source") in ("sensor", "twin", "forecast"):
            self.derate_source = msg["derate_source"]
        return changed


def list_datasets() -> list[dict]:
    out = []
    for d in dataset_dirs():
        for f in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
    return out


def load_dataset(name: str) -> tuple[dict, list[dict]]:
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("잘못된 이름")
    d = next((d for d in dataset_dirs() if (d / f"{name}.json").exists()), None)
    if d is None:
        raise ValueError(f"실차 데이터 {name} 이 없습니다")
    meta = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
    with open(d / f"{name}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return meta, rows


class Engine:
    def __init__(self, vehicle_id: str = "default", source: str = "sim", shared: "Engine | None" = None,
                 store: LogStore | None = None):
        """shared: 다른 엔진의 학습 모델(PINN·공분산)을 같이 씀 (차량을 여러 대 받을 때 메모리 절약)"""
        self.vehicle_id = vehicle_id
        self.ctrl = EngineControls(source=source)
        if shared is not None:
            self.pinn, self.sim_cov, self.real_cov = shared.pinn, shared.sim_cov, shared.real_cov
            self.store = store or shared.store
        else:
            self.pinn = PINNVirtualSensor()
            self.sim_cov = est.load_spatial_cov()
            rc = MODEL_DIR / "real_cov.npz"
            self.real_cov = np.load(rc)["cov"] if rc.exists() else None
            self.store = store
        self.sensor_cells: list[int] = est.uniform_layout(16)
        self.running = True
        self.log_rows: list[list] = []
        self.ingest_state = {"connected": 0, "last_rx": None, "samples": 0, "rejected": 0, "warnings": []}
        self.consec_rejects = 0
        # 사용자가 화면에서 소스를 직접 고르면 True → 외부 데이터가 이 차량을 가로채지 않음 (main.py 참고)
        self.user_source_hold = False
        self.redirected_samples = 0
        self.commands = {}
        self.reset()

    # ── 초기화 / 소스 전환 ──
    def reset(self) -> None:
        c = self.ctrl
        self.vehicle = SimVehicle()
        self.vehicle.ctrl.mode = c.mode
        for k in ("charge_power_kw", "ambient_c", "coolant_set_c", "coolant_flow_lpm", "channel_width_mm",
                  "channel_depth_mm", "true_soh"):
            setattr(self.vehicle.ctrl, k, getattr(c, k))
        self.vehicle.reset()
        real = c.source in ("replay", "ingest")
        self.twin = TwinEstimator(c.channel_width_mm, c.channel_depth_mm, 30.0 if real else c.coolant_flow_lpm,
                                  sensor_noise=0.45 if real else 0.3, pinn=self.pinn,
                                  gp_cov=self.real_cov if (real and self.real_cov is not None) else self.sim_cov,
                                  use_voltage=c.use_voltage, ocv_known=not real, k_prior=1.6 if real else 1.0)
        self.t = 0.0
        self.sample_acc = 0.0
        self.last = None
        self.last_sample = None
        self.truth = None
        self.holdout = {"err2": 0.0, "n": 0, "base2": 0.0}
        self.replay = None
        self.prev_ingest = None
        self.risk = ("Safe", [])
        self.runaway = None
        self.fallback = {"active": False, "reasons": [], "since_t": None, "bad": 0, "good_since": None, "health": {}}
        self._forecast = None
        self._fc_t = -1e9
        self.base_flow = c.coolant_flow_lpm
        if c.source == "replay":
            self._open_replay(c.dataset)
        self.commands = {"charge_limit_kw": c.charge_power_kw, "coolant_flow_lpm": c.coolant_flow_lpm,
                         "coolant_set_c": c.coolant_set_c, "derate": 1.0, "reason": "정상", "mode": "twin"}

    def _open_replay(self, name: str) -> None:
        meta, rows = load_dataset(name)
        probe_cells = [est.cell_index(*rc) for rc in meta["probe_cells"]]
        # 기본: 프로브 32개 중 절반(16개)만 트윈에 주고 나머지는 검증용으로 숨김
        use = [probe_cells[k] for k in range(0, len(probe_cells), 2)]
        self.replay = {"name": name, "meta": meta, "rows": rows, "i": 0, "probe_cells": probe_cells}
        self.sensor_cells = use

    def apply_control(self, msg: dict) -> None:
        old_source, old_ds = self.ctrl.source, self.ctrl.dataset
        # 소스를 먼저 바꿔야 새 소스 기준으로 잠금 항목이 정해짐 (예: 외부 수신 → 시뮬레이션 + 운전 모드 변경)
        if msg.get("source") in ("sim", "replay", "ingest"):
            self.ctrl.source = msg["source"]
        if msg.get("dataset"):
            self.ctrl.dataset = str(msg["dataset"])
        changed = self.ctrl.update(msg)
        if self.ctrl.source != old_source or (self.ctrl.source == "replay" and self.ctrl.dataset != old_ds):
            if self.ctrl.source == "sim":
                self.sensor_cells = est.uniform_layout(16)
            self.reset()
            self.running = True          # 재생이 끝나 멈춘 뒤 다른 데이터를 골라도 바로 시작
            return
        v = self.vehicle
        for k in ("charge_power_kw", "ambient_c", "coolant_set_c", "channel_width_mm", "channel_depth_mm"):
            setattr(v.ctrl, k, getattr(self.ctrl, k))
        if "coolant_flow_lpm" in changed:
            v.ctrl.coolant_flow_lpm = self.ctrl.coolant_flow_lpm
            self.base_flow = self.ctrl.coolant_flow_lpm
        if "true_soh" in changed:
            v.set_soh(self.ctrl.true_soh)
        if "mode" in changed:
            v.mode = v.ctrl.mode = self.ctrl.mode
            v.note = ""
        if changed & {"channel_width_mm", "channel_depth_mm", "coolant_flow_lpm"} and self.ctrl.source == "sim":
            self.twin.set_design(self.ctrl.channel_width_mm, self.ctrl.channel_depth_mm, v.ctrl.coolant_flow_lpm)
        if "use_voltage" in changed:
            self.twin.use_voltage = self.ctrl.use_voltage

    def set_sensors(self, cells: list[list[int]]) -> None:
        idx = [est.cell_index(int(r), int(c)) for r, c in cells if 0 <= int(r) < tm.N_ROWS and 0 <= int(c) < tm.N_COLS]
        if self.replay:       # 재생: 실제로 프로브가 있는 위치만 선택 가능
            idx = [i for i in idx if i in self.replay["probe_cells"]]
        self.sensor_cells = list(dict.fromkeys(idx))

    # ── 1 tick (실제 1초) ──
    def tick(self) -> dict | None:
        if not self.running:
            return None
        c = self.ctrl
        if c.source == "sim":
            v = self.vehicle
            for _ in range(int(c.time_scale)):
                self._apply_commands_to_vehicle()
                v.step(1.0)
                self.sample_acc += 1.0
                if self.sample_acc >= SIM_SAMPLE_DT:
                    self.sample_acc = 0.0
                    self._process(v.telemetry(self.sensor_cells))
            self.truth = v.truth()
        elif c.source == "replay" and self.replay:
            rp = self.replay
            n_rows = max(1, int(round(c.time_scale / 10.0)))
            for _ in range(n_rows):
                if rp["i"] >= len(rp["rows"]):
                    self.running = False
                    break
                self._process(self._replay_sample(rp["rows"][rp["i"]]))
                rp["i"] += 1
        if self.last is None:
            return None
        return self.frame()

    def _replay_sample(self, row: dict) -> dict:
        rp = self.replay
        probes = {cell: float(row[f"tp_{k + 1:02d}"]) for k, cell in enumerate(rp["probe_cells"])}
        use = [i for i in self.sensor_cells if i in probes]
        s = {"t_s": float(row["t_s"]), "current_a": float(row["current_a"]), "soc": float(row["soc_pct"]) / 100,
             "v_cells": [float(row[f"v_{i:02d}"]) for i in range(1, 97)],
             "probe_idx": use, "probe_t": [probes[i] for i in use], "speed_kmh": float(row.get("speed_kmh") or 0)}
        s["_holdout"] = {i: t for i, t in probes.items() if i not in use}
        s["_all_probes"] = probes
        return s

    def ingest(self, s: dict) -> dict:
        """외부 게이트웨이 텔레메트리 1건 처리 → 제어 명령 반환. 타당하지 않은 샘플은 ValueError 로 거부"""
        if self.ctrl.source != "ingest":
            self.apply_control({"source": "ingest"})
        s = dict(s)
        prev_t = self.prev_ingest.get("t_s") if self.prev_ingest else None
        if prev_t is not None and s.get("t_s") is not None and float(s["t_s"]) < prev_t - ingest_guard.SESSION_RESTART_BACKSTEP_S:
            # 시각이 크게 되돌아감 = BMS·게이트웨이 재시작. 거부하지 않고 이 차량의 트윈을 새로 시작
            if self.store:
                self.store.event(self.vehicle_id, "restart", f"t_s {prev_t:g} → {float(s['t_s']):g}: 새 세션으로 트윈 초기화")
            counts = {k: self.ingest_state[k] for k in ("samples", "rejected")}
            self.reset()
            self.ingest_state.update(counts)
        try:
            warnings = ingest_guard.validate(s, self.prev_ingest)
        except ValueError as e:
            self.ingest_state["rejected"] += 1
            self.consec_rejects += 1
            if self.store:
                self.store.event(self.vehicle_id, "rejected", str(e))
            raise
        self.consec_rejects = 0
        if s.get("t_s") is None:
            s["t_s"] = self.t + 10.0 if self.last is not None else 0.0
        self.prev_ingest = s
        sample = {
            "t_s": s["t_s"], "current_a": s["current_a"], "soc": s["soc"], "v_cells": s.get("v_cells"),
            "probe_idx": [est.cell_index(int(r), int(c)) for r, c in s.get("probe_cells") or []],
            "probe_t": s["probe_t"], "t_coolant_in": s.get("t_coolant_in"), "t_ambient": s.get("t_ambient"),
            "coolant_flow_lpm": s.get("coolant_flow_lpm"),
        }
        self.sensor_cells = sample["probe_idx"]
        self.ingest_state["last_rx"] = time.time()
        self.ingest_state["samples"] += 1
        self.ingest_state["warnings"] = warnings
        if warnings and self.store:
            for w in warnings:
                self.store.event(self.vehicle_id, "warning", w)
        self._process(sample)
        return {**self.commands, "vehicle_id": self.vehicle_id, "risk_status": self.risk[0], "warnings": warnings}

    # ── 샘플 처리: 추정 + 판정 + 명령 ──
    def _process(self, s: dict) -> None:
        out = self.twin.step(s)
        self.last, self.last_sample = out, s
        self.t = float(s["t_s"])
        if "_holdout" in s and s["_holdout"]:
            cells = list(s["_holdout"].keys())
            truth = np.array(list(s["_holdout"].values()))
            e = out["surf"][cells] - truth
            used = np.array(s["probe_t"]) if s["probe_t"] else np.array([truth.mean()])
            self.holdout["err2"] += float(np.sum(e**2))
            self.holdout["base2"] += float(np.sum((used.mean() - truth) ** 2))
            self.holdout["n"] += len(cells)
        self._record_fault_events(out)
        self._update_failsafe(out, s)
        self._commands(out, s)
        self.risk = self._assess_risk(out, s)
        self._log(out, s)

    def core_estimate(self, out: dict) -> np.ndarray:
        """대표 코어온도 = PINN 이 학습한 R_in·C 로 보정한 물리 모델 (검증 결과 가장 정확)"""
        return out["core_phys"]

    # ── 안전 모드(Fail-safe) ──
    def _health(self, out: dict, s: dict) -> tuple[dict, list[str]]:
        """트윈(AI) 추정을 믿을 수 있는지 매 샘플 점검 → (지표, 안전 모드로 가야 할 이유)"""
        gap = float(np.abs(out["core_pinn"] - self.core_estimate(out)).max())
        std = float(np.mean(out["post_std"]))
        resid = out["residual_rmse"]
        n_sensors = out["faults"]["n_used"]          # 고장으로 격리한 센서는 빼고 셈
        h = {"model_gap_k": round(gap, 2), "mean_std_k": round(std, 3),
             "residual_rmse_k": round(resid, 2) if resid is not None else None,
             "n_sensors": n_sensors, "consec_rejects": self.consec_rejects,
             "limits": {"model_gap_k": FALLBACK_GAP_K, "mean_std_k": FALLBACK_STD_K,
                        "residual_rmse_k": FALLBACK_RESID_K, "consec_rejects": FALLBACK_REJECT_N}}
        why = []
        if gap >= FALLBACK_GAP_K:
            why.append(f"PINN 신경망·하이브리드 코어 차이 {gap:.1f} K ≥ {FALLBACK_GAP_K:.0f} K")
        if n_sensors == 0:
            why.append("쓸 수 있는 온도센서 없음 (입력 없음 또는 모두 고장 격리) — 트윈을 실측으로 보정할 수 없음")
        elif std >= FALLBACK_STD_K:
            why.append(f"추정 불확실성 σ {std:.2f} K ≥ {FALLBACK_STD_K:.1f} K")
        if resid is not None and resid >= FALLBACK_RESID_K:
            why.append(f"센서 실측과 모델 차이 {resid:.1f} K ≥ {FALLBACK_RESID_K:.0f} K (모델 발산 의심)")
        if self.consec_rejects >= FALLBACK_REJECT_N:
            why.append(f"입력 연속 거부 {self.consec_rejects}회 (센서·통신 이상)")
        r = FALLBACK_CLEAR_RATIO
        h["clear"] = (not why and n_sensors > 0 and self.consec_rejects == 0 and gap < r * FALLBACK_GAP_K
                      and std < r * FALLBACK_STD_K and (resid is None or resid < r * FALLBACK_RESID_K))
        return h, why

    def _record_fault_events(self, out: dict) -> None:
        if self.store:
            for ev in out["faults"]["events"]:
                self.store.event(self.vehicle_id, "sensor_fault" if "격리" in ev else "sensor_ok", ev)

    def _update_failsafe(self, out: dict, s: dict) -> None:
        """진입: 이상 조건이 연속 FALLBACK_ENTER_N 샘플 / 해제: 모든 지표가 한계의 70 % 아래로 FALLBACK_EXIT_S 동안 유지.
        진입·해제 기준을 다르게 두어(히스테리시스) 한계 근처에서 모드가 깜빡이지 않게 함"""
        fb = self.fallback
        health, why = self._health(out, s)
        fb["health"] = health
        if self.ctrl.force_fallback:
            why = ["사용자가 안전 모드를 강제함", *why]
        was = fb["active"]
        if why:
            fb["bad"], fb["good_since"] = fb["bad"] + 1, None
            fb["reasons"] = why
            if self.ctrl.force_fallback or fb["bad"] >= FALLBACK_ENTER_N:
                fb["active"] = True
        else:
            fb["bad"] = 0
            if not health["clear"]:
                fb["good_since"] = None          # 한계 아래지만 여유가 없음 → 유지
            elif fb.get("good_since") is None:
                fb["good_since"] = self.t
            if fb["active"] and fb.get("good_since") is not None and self.t - fb["good_since"] >= FALLBACK_EXIT_S:
                fb["active"], fb["reasons"] = False, []
        if fb["active"] != was:
            fb["since_t"] = self.t if fb["active"] else None
            if self.store:
                self.store.event(self.vehicle_id, "fallback_on" if fb["active"] else "fallback_off",
                                 "; ".join(fb["reasons"]) if fb["active"] else "트윈 추정 정상화 — AI 제어로 복귀")

    def _commands(self, out: dict, s: dict) -> None:
        c = self.ctrl
        core = self.core_estimate(out)
        core_safe = np.maximum(core, out["core_pinn"])          # 위험 판정은 두 추정 중 높은 값 (보수적)
        sensor_max = max(s["probe_t"]) if s["probe_t"] else None
        self._forecast = self._run_forecast(s)
        fallback = self.fallback["active"]
        if fallback:
            # 규칙 기반: 센서 실측 최고값 + 여유. 트윈 추정이 더 높으면 그 값 (어느 쪽이 틀려도 보수적인 쪽으로)
            # 안전 모드는 디레이팅 설정과 상관없이 항상 적용
            basis = "rule"
            t_rule = (sensor_max if sensor_max is not None else float(core_safe.max())) + FALLBACK_MARGIN_K
            t_ctrl = max(t_rule, float(core_safe.max()))
            derate = float(tm.thermal_derate(np.array([t_ctrl]))[0])
            flow = float(min(30.0, max(self.base_flow, FALLBACK_FLOW_LPM)))
            src = f"센서 최고 + {FALLBACK_MARGIN_K:.0f} K" if t_rule >= float(core_safe.max()) else "트윈 코어(더 높음)"
            reason = f"안전 모드(규칙 기반): {src} = {t_ctrl:.1f} °C → 출력 {derate * 100:.0f} %"
        else:
            basis = c.derate_source
            if c.derate_source == "sensor":
                t_ctrl = sensor_max if sensor_max is not None else float(out["surf"].max())
            elif c.derate_source == "forecast" and self._forecast is not None:
                t_ctrl = max(float(core_safe.max()), float(self._forecast.max()))
            else:
                t_ctrl = float(core_safe.max())
            derate = float(tm.thermal_derate(np.array([t_ctrl]))[0]) if c.derating else 1.0
            flow = self.base_flow
            if c.auto_pump:
                flow = float(np.clip(self.base_flow + 2.0 * (core_safe.max() - 35.0), self.base_flow, 30.0))
            reason = "정상"
            if derate < 0.999:
                reason = {"sensor": "센서 최고온도", "twin": "트윈 코어 추정", "forecast": f"{FORECAST_MIN}분 뒤 예측"}[c.derate_source] \
                    + f" {t_ctrl:.1f} °C → 출력 {derate * 100:.0f} %"
        mode = "fallback" if fallback else ("twin" if (c.derating or c.auto_pump) else "off")
        self.commands = {"charge_limit_kw": round(c.charge_power_kw * derate, 1), "coolant_flow_lpm": round(flow, 1),
                         "coolant_set_c": c.coolant_set_c, "derate": round(derate, 3), "reason": reason,
                         "t_control_c": round(t_ctrl, 2), "issued_at": round(self.t, 1),
                         "mode": mode, "basis": basis, "fallback_reasons": list(self.fallback["reasons"]) if fallback else []}

    def command_status(self) -> dict:
        """지금 명령이 유효한지: 외부 수신이 끊기면 마지막 명령은 무효 (BMS 는 자체 규칙으로 동작해야 함)"""
        c = self.ctrl
        age = time.time() - self.ingest_state["last_rx"] if self.ingest_state["last_rx"] else None
        stale = c.source == "ingest" and (age is None or age > STALE_S)
        applied = {"sim": "가상 차량에 매 초 바로 적용 (폐루프)",
                   "ingest": "차량 BMS 에 권고로 전송 (TT_Command)",
                   "replay": "기록 재생이라 계산만 하고 적용하지 않음"}[c.source]
        return {"mode": self.commands.get("mode", "twin"), "basis": self.commands.get("basis"),
                "valid": not stale, "stale": stale, "age_s": round(age, 1) if age is not None else None,
                "stale_after_s": STALE_S, "applied": applied, "closed_loop": c.source == "sim",
                "fallback": {"active": self.fallback["active"], "forced": c.force_fallback,
                             "reasons": list(self.fallback["reasons"]), "since_t": self.fallback["since_t"],
                             "health": self.fallback["health"]}}

    def _run_forecast(self, s: dict):
        """현재 전류가 이어진다고 가정한 10분 예측 (PINN 보정 물리 모델). 30초마다 갱신"""
        if getattr(self, "_fc_t", -1e9) + 30 > float(s["t_s"]) and getattr(self, "_forecast", None) is not None:
            return self._forecast
        self._fc_t = float(s["t_s"])
        i_now = float(s["current_a"])
        if self.ctrl.source == "sim" and self.vehicle.mode == "fast_charge":
            soc = float(s["soc"])
            prof = []
            for _ in range(FORECAST_MIN * 6):
                p = self.ctrl.charge_power_kw * float(tm.fast_charge_limit_w(np.array([soc]))[0]) * 1000
                cur = -p / float(tm.pack_ocv(np.array([soc]))[0]) if soc < 0.8 else 0.0
                soc = soc - cur * 10 / 3600 / tm.CELL_CAPACITY_AH
                prof.append(cur)
        else:
            prof = [i_now] * (FORECAST_MIN * 6)
        t_in = s.get("t_coolant_in")
        return self.twin.forecast(prof, 10.0, t_in)

    def _apply_commands_to_vehicle(self) -> None:
        v, c = self.vehicle, self.ctrl
        fallback = self.commands.get("mode") == "fallback"
        v.power_limit_kw = self.commands.get("charge_limit_kw") if (c.derating or fallback) else None
        if c.auto_pump or fallback:
            v.ctrl.coolant_flow_lpm = self.commands.get("coolant_flow_lpm", v.ctrl.coolant_flow_lpm)
        else:
            v.ctrl.coolant_flow_lpm = self.base_flow

    def _log(self, out: dict, s: dict) -> None:
        core = self.core_estimate(out)
        row = [round(self.t, 1), self.ctrl.source, round(float(s["current_a"]), 1), round(float(s["soc"]) * 100, 1),
               round(float(core.max()), 2), round(float(out["core_pinn"].max()), 2), round(float(out["surf"].max()), 2),
               round(max(s["probe_t"]), 2) if s["probe_t"] else "",
               round(out["soh_est"] * 100, 1) if out["soh_est"] is not None else "",
               self.commands.get("charge_limit_kw"), self.commands.get("coolant_flow_lpm")]
        self.log_rows.append(row)
        if len(self.log_rows) > 200_000:
            self.log_rows = self.log_rows[-100_000:]
        if self.store:
            self.store.add({
                "vehicle_id": self.vehicle_id, "wall_time": time.time(), "t_s": row[0], "source": row[1],
                "current_a": row[2], "soc_pct": row[3], "core_max_hybrid_c": row[4], "core_max_pinn_nn_c": row[5],
                "surf_max_c": row[6], "sensor_max_c": row[7] if row[7] != "" else None,
                "soh_est_pct": row[8] if row[8] != "" else None,
                "risk_status": self.risk[0], "runaway_hours_adiabatic": self.runaway["adiabatic_hours"] if self.runaway else None,
                "cmd_charge_limit_kw": row[9], "cmd_coolant_flow_lpm": row[10]})

    def _assess_risk(self, out: dict, s: dict) -> tuple[str, list[str]]:
        """위험 판정: 설계 한계값(45/55 °C) + 열폭주 반응열 모델 + 셀 편차·모델 불일치·저온 충전 + 입력 이상"""
        core = self.core_estimate(out)
        core_safe = np.maximum(core, out["core_pinn"])
        core_max = float(core_safe.max())
        delta_t = float(out["surf"].max() - out["surf"].min())
        gap = float(np.abs(out["core_pinn"] - core).max())
        current = float(s["current_a"])
        order = {"Safe": 0, "Warning": 1, "Danger": 2}

        status, reasons = "Safe", []

        def raise_to(level: str) -> None:
            nonlocal status
            if order[level] > order[status]:
                status = level

        if core_max >= T_CORE_DANGER_C:
            raise_to("Danger")
            reasons.append(f"추정 코어 최고온도 {core_max:.1f} °C ≥ {T_CORE_DANGER_C:.0f} °C")
        elif core_max >= T_CORE_WARNING_C:
            raise_to("Warning")
            reasons.append(f"추정 코어 최고온도 {core_max:.1f} °C ≥ {T_CORE_WARNING_C:.0f} °C")
        self.runaway = runaway.assess(core_safe, self.pinn.R_in)
        ra = self.runaway
        if ra["level"] != "Safe":
            raise_to(ra["level"])
            cell = est.index_cell(ra["hottest_cell"])
            reasons.append(f"열폭주 반응열: M{cell[0] + 1}-C{cell[1] + 1} 냉각 상실 시 약 {ra['adiabatic_hours']:.1f} 시간 뒤 폭주"
                           + ("" if ra["cooled_stable"] else " · 냉각으로도 자기발열을 못 이기는 온도"))
        if delta_t >= DELTA_T_WARNING_K:
            raise_to("Warning")
            reasons.append(f"셀 간 온도편차 {delta_t:.1f} K ≥ {DELTA_T_WARNING_K:.0f} K")
        if gap >= MODEL_GAP_WARNING_K and not self.fallback["active"]:
            # 참고 정보: 상태를 올리지 않음 (계산 신뢰도는 안전 모드 3 K 기준이 맡음 — 배터리가 위험한 것이 아님)
            reasons.append(f"참고: 두 계산 방식(PINN 신경망·하이브리드)의 코어 추정이 {gap:.1f} K 차이 — "
                           f"{FALLBACK_GAP_K:.0f} K 를 넘으면 안전 모드로 바뀝니다")
        if current < -0.3 * tm.CELL_CAPACITY_AH and float(core.min()) < 10.0:
            raise_to("Warning")
            reasons.append("저온 충전 — 리튬 석출 위험")
        if self.ctrl.source == "ingest":
            for w in self.ingest_state["warnings"]:
                raise_to("Warning")
                reasons.append(f"입력 이상: {w}")
        local = {f"{a}-{b}" for a, b in out["faults"].get("local_sensors", [])}
        for cell, why in out["faults"]["bad_reasons"].items():
            raise_to("Warning")
            r_, c_ = (int(x) for x in cell.split("-"))
            reasons.append(f"온도센서 M{r_ + 1}-C{c_ + 1} {'이상 (국부 반영)' if cell in local else '고장 의심 → 보정에서 제외'}: {why}")
        if self.fallback["active"]:
            raise_to("Warning")
            reasons.insert(0, "안전 모드 — AI 추정 대신 규칙 기반 제어 중 (이유는 '제어 상태' 참고)")
        if self.commands.get("derate", 1) < 0.999:
            reasons.append(f"제어 명령: {self.commands['reason']}")
        return status, reasons

    def log_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["t_s", "source", "current_a", "soc_pct", "core_max_hybrid_c", "core_max_pinn_nn_c", "surf_max_c",
                    "sensor_max_c", "soh_est_pct", "cmd_charge_limit_kw", "cmd_coolant_flow_lpm"])
        w.writerows(self.log_rows)
        return buf.getvalue()

    # ── 화면용 데이터 ──
    def frame(self) -> dict:
        c, out, s = self.ctrl, self.last, self.last_sample
        core = self.core_estimate(out)
        core_safe = np.maximum(core, out["core_pinn"])
        grid = lambda a, n=2: np.round(np.asarray(a).reshape(tm.N_ROWS, tm.N_COLS), n).tolist()  # noqa: E731
        pct = lambda v: round(v * 100, 1) if v is not None else None  # noqa: E731
        core_max = float(core_safe.max())
        hot = divmod(int(np.argmax(core_safe)), tm.N_COLS)
        delta_t = float(out["surf"].max() - out["surf"].min())
        gap = float(np.abs(out["core_pinn"] - core).max())
        current = float(s["current_a"])
        status, reasons = self.risk
        control = self.command_status()
        if control["stale"] and self.last is not None:
            status = "Warning" if status == "Safe" else status
            reasons = [f"외부 데이터 끊김 {control['age_s']} s — 마지막 명령 무효, BMS 자체 규칙으로 동작해야 함", *reasons]

        fc = self._forecast if getattr(self, "_forecast", None) is not None else np.zeros(0)
        to_warn = None
        if len(fc):
            hit = np.nonzero(fc >= T_CORE_WARNING_C)[0]
            to_warn = round(float((hit[0] + 1) * 10 / 60), 1) if len(hit) and core_max < T_CORE_WARNING_C else None
        weak = np.argsort(-out["k_cells"])[:3]

        ra = self.runaway or runaway.assess(core_safe, self.pinn.R_in)
        f = {
            "vehicle_id": self.vehicle_id,
            "t_sim_s": round(self.t, 1), "source": c.source, "running": self.running,
            "mode": self.vehicle.mode if c.source == "sim" else ("fast_charge" if current < -5 else ("rest" if abs(current) < 5 else "highway")),
            "status_note": self.vehicle.note if c.source == "sim" else "",
            "risk_status": status, "risk_reasons": reasons,
            "current_a": round(current, 1), "power_kw": round(-current * float(tm.pack_ocv(np.array([s["soc"]]))[0]) / 1000, 1),
            "current_soc": round(float(s["soc"]) * 100, 1), "c_rate": round(abs(current) / tm.CELL_CAPACITY_AH, 2),
            "heat_kw": round(float(out["q"].sum()) / 1000, 2),
            "coolant_in_c": s.get("t_coolant_in") if s.get("t_coolant_in") is not None else round(out["t_boundary"], 2),
            "kpi": {"core_max_c": round(core_max, 2), "core_hybrid_max_c": round(float(core.max()), 2),
                    "core_nn_max_c": round(float(out["core_pinn"].max()), 2), "surf_max_c": round(float(out["surf"].max()), 2),
                    "sensor_max_c": round(max(s["probe_t"]), 2) if s["probe_t"] else None, "delta_t_k": round(delta_t, 2),
                    "hotspot": [hot[0], hot[1]], "model_gap_k": round(gap, 2), "n_sensors": len(s["probe_idx"]),
                    "mean_std_k": round(float(np.mean(out["post_std"])), 3)},
            "field": {"core": grid(core), "core_nn": grid(out["core_pinn"]), "surf": grid(out["surf"]),
                      "std": grid(out["post_std"], 3), "k_cells": grid(out["k_cells"], 3),
                      "q": grid(out["q"], 1)},
            "sensors": [est.index_cell(i) for i in s["probe_idx"]],
            "twin": {"soh_est": pct(out["soh"]["soh"]), "soh_lo": pct(out["soh"]["lo"]), "soh_hi": pct(out["soh"]["hi"]),
                     "soh_status": out["soh"]["status"], "soh_updates": out["soh"]["n_used"],
                     "soh_skipped": out["soh"]["n_skipped"], "soh_raw": pct(out["soh_raw"]),
                     "weak_cells": [est.index_cell(int(i)) for i in weak],
                     "k_mean": round(float(self.twin.rls.k_mean), 3), "residual_rmse": out["residual_rmse"],
                     "R_in": round(self.pinn.R_in, 4), "C_core": round(self.pinn.C, 1), "pinn_ok": self.pinn.ok},
            "forecast": {"horizon_min": FORECAST_MIN, "series": np.round(fc, 2).tolist(),
                         "peak_c": round(float(fc.max()), 2) if len(fc) else None, "minutes_to_warning": to_warn},
            "faults": {k: v for k, v in out["faults"].items() if k != "events"},
            "runaway": {**{k: ra[k] for k in ("level", "adiabatic_hours", "onset_c", "margin_to_onset_k",
                                              "semenov_critical_c", "cooled_stable")},
                        "self_heat_max_w": round(ra["self_heat_max_w"], 4),
                        "hottest": est.index_cell(ra["hottest_cell"])},
            "commands": self.commands, "controls": asdict(c), "control": control,
            "input": {"source": c.source, "locked": sorted(LOCKED_BY_SOURCE.get(c.source, set())),
                      "user_hold": self.user_source_hold, "redirected_samples": self.redirected_samples,
                      "rule": "실측(외부 수신·실차 재생) > 화면 설정. 화면에서 소스를 직접 고르면 그 선택이 유지되고, "
                              "기본 차량으로 오는 외부 데이터는 'external' 차량으로 따로 받습니다."},
            "ingest": {**self.ingest_state, "age_s": round(time.time() - self.ingest_state["last_rx"], 1) if self.ingest_state["last_rx"] else None},
            "log_rows": len(self.log_rows),
        }
        if c.source == "sim" and self.truth is not None:
            tr, v = self.truth, self.vehicle
            cool = v.cooling()
            f["field"]["true_core"] = grid(tr["core"])
            f["field"]["true_surf"] = grid(tr["surf"])
            f["twin"].update({"soh_true": round(c.true_soh * 100, 1),
                              "core_rmse_k": round(float(np.sqrt(np.mean((core - tr["core"]) ** 2))), 3),
                              "core_nn_rmse_k": round(float(np.sqrt(np.mean((out["core_pinn"] - tr["core"]) ** 2))), 3),
                              "core_max_err_k": round(float(np.abs(core - tr["core"]).max()), 2),
                              "true_core_max_c": round(float(tr["core"].max()), 2),
                              "true_weak_cells": [[int(a), int(b)] for a, b in np.argwhere(v.plant.r_scale[0] > 1.2)]})
            f.update({"coolant_out_c": round(tr["coolant_out"], 2), "chiller_kw": round(tr["chiller_w"] / 1000, 2),
                      "cooling_kw": round(tr["q_cool"] / 1000, 2),
                      "flow": {"velocity_ms": round(float(cool.velocity[0].mean()), 3), "reynolds": round(float(cool.reynolds[0].mean())),
                               "dp_kpa": round(float(cool.dp_pa[0]) / 1000, 2), "pump_electric_w": round(float(cool.pump_electric_w[0]), 1),
                               "flow_lpm": v.effective_flow_lpm, "flow_requested_lpm": v.ctrl.coolant_flow_lpm}})
        if c.source == "replay" and self.replay:
            rp = self.replay
            h = self.holdout
            f["replay"] = {"name": rp["name"], "title": rp["meta"]["title"], "row": rp["i"], "rows": len(rp["rows"]),
                           "available_probes": [est.index_cell(i) for i in rp["probe_cells"]],
                           "holdout_rmse_k": round(float(np.sqrt(h["err2"] / h["n"])), 3) if h["n"] else None,
                           "mean_baseline_rmse_k": round(float(np.sqrt(h["base2"] / h["n"])), 3) if h["n"] else None,
                           "holdout_probes": [est.index_cell(i) for i in (s.get("_holdout") or {})],
                           "probe_values": {f"{a}-{b}": round(t, 1) for (a, b), t in
                                            ((est.index_cell(i), t) for i, t in (s.get("_all_probes") or {}).items())},
                           "citation": rp["meta"].get("citation")}
        return f

    def _cov_scale(self) -> float:
        return 1.0   # GP 사후분산은 이미 K² 단위 (오차 크기를 실시간 적응)
