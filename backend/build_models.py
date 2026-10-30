"""
모델 학습·검증 스크립트 — 결과는 backend/models/ 에 저장되고 웹 화면이 그대로 읽습니다.

  python build_models.py            # 전부 (약 2~4분)
  python build_models.py pinn       # PINN 만 다시 학습
  python build_models.py cov        # 공간 공분산 + 센서 배치 곡선
  python build_models.py validate   # 시뮬레이션 폐루프 검증 + 실차 데이터 검증

만드는 파일:
  models/pinn.npz, pinn.json        PINN 가중치 · 학습된 R_in/C · 성능 비교
  models/spatial_cov.npz            센서 오차 공간 공분산 (GP 보정·센서 배치에 사용)
  models/placement.json             센서 개수별 예상/실측 오차 곡선, 최적 배치
  models/real_validation.json       실차 데이터: 일부 프로브만 쓰고 나머지로 오차 검증
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

import estimator as est
import pinn
import scenarios as sc
import thermal_model as tm
from sim_vehicle import SimVehicle
from twin import TwinEstimator

MODEL_DIR = Path(__file__).resolve().parent / "models"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "real_ev"
N_COUNTS = [2, 4, 8, 12, 16, 24, 32, 48]


def log(*a):
    print(*a, flush=True)


# ─────────────────────────────────────────────
def build_pinn() -> dict:
    t0 = time.time()
    tr = sc.run_ensemble(48, 1)
    te = sc.run_ensemble(16, 2)
    ood = sc.run_ensemble(16, 3, power_range=(180, 230), ambient_range=(30, 45))
    rng = np.random.default_rng(0)
    pairs = pinn.make_pairs(tr, lag=3, rng=rng, frac=0.25)
    labeled = np.isin(pairs["b"], np.arange(10))      # 48 시나리오 중 10개만 코어 열전대 데이터(실험실 셀)
    net_d, _, _ = pinn.train(pairs, labeled, use_physics=False, iters=3000)
    net, phys, hist = pinn.train(pairs, labeled, use_physics=True, iters=3000)
    ev = {}
    for name, ens in (("test", te), ("ood", ood)):
        e_p, e_d = pinn.evaluate(net, ens), pinn.evaluate(net_d, ens)
        dt = float(ens["dt_sample"])
        rb = pinn.physics_core_rollout(ens["ts"], ens["q"], dt, phys["R_in"], phys["C"])
        warm = int(300 / dt)
        ev[name] = {
            "pinn_rmse": e_p["pinn_rmse"], "pinn_max": e_p["pinn_max"],
            "data_only_rmse": e_d["pinn_rmse"], "data_only_max": e_d["pinn_max"],
            "datasheet_physics_rmse": e_p["phys_rmse"], "datasheet_physics_max": e_p["phys_max"],
            "learned_physics_rmse": float(np.sqrt(np.mean((rb[warm:] - ens["tc"][warm:]) ** 2))),
            "surface_as_core_rmse": e_p["surface_only_rmse"],
            "peak_core_true": e_p["peak_core_true"], "peak_core_pinn": e_p["peak_core_pinn"],
        }
    report = {
        "trained_at": time.strftime("%Y-%m-%d %H:%M"),
        "train": {"scenarios": 48, "labeled_scenarios": 10, "pairs": int(len(labeled)),
                  "labeled_pairs": int(labeled.sum()), "iters": 3000, "seconds": round(time.time() - t0, 1)},
        "params": {
            "R_in_datasheet": tm.R_IN, "R_in_learned": phys["R_in"], "R_in_true": float(tr["r_in"].mean()),
            "C_datasheet": tm.C_CORE, "C_learned": phys["C"], "C_true": float(tr["c_core"].mean()),
        },
        "eval": ev,
        "history": hist,
        "arch": {"features": ["Ts", "q", "EMA q 60s", "EMA q 200s", "EMA q 600s", "EMA q 1500s",
                              "EMA dTs/dt 60s", "EMA dTs/dt 200s", "EMA dTs/dt 600s", "EMA dTs/dt 1500s",
                              "SOC", "|C-rate|"], "layers": [pinn.N_FEAT, pinn.HIDDEN, pinn.HIDDEN, 1]},
    }
    pinn.save(net, phys, report)
    log("PINN", json.dumps(report["params"]), json.dumps(ev, indent=0)[:600])
    return report


# ─────────────────────────────────────────────
def build_cov() -> np.ndarray:
    """실제 차량(무작위 불확실성)과 트윈(설계값, 셀 저항은 전압으로 안다고 가정)의 표면온도 차이 통계"""
    p = pinn.PINNVirtualSensor()
    snaps = []
    for seed in (11, 12, 13):
        n = 32
        rng = np.random.default_rng(seed)
        mode = np.array(["fast_charge", "aggressive", "fast_charge", "highway"] * (n // 4))
        amb = rng.uniform(-5, 38, n)
        power = rng.uniform(60, 200, n)
        flow = rng.uniform(4, 25, n)
        set_c = rng.uniform(15, 30, n)
        soh = rng.uniform(0.72, 1.0, n)
        w = rng.choice([6.0, 8.0, 10.0], n)
        d = rng.choice([2.0, 3.0, 4.0], n)
        soc0 = np.where(mode == "fast_charge", 0.1, 0.9)
        plant, share = sc.make_plant(n, rng, amb, soc0, soh)
        cool_p = tm.cooling_plate(w, d, flow, share)
        cool_t = tm.cooling_plate(w, d, flow)                 # 트윈: 설계 유량 분배
        twin = tm.PackThermalModel(n, amb, soc0)
        twin.r_in[:], twin.c_core[:] = p.R_in, p.C
        for k in range(900):
            t = k * 2.0
            pw = np.zeros(n)
            for i in range(n):
                if mode[i] == "fast_charge":
                    if plant.soc[i] < 0.8:
                        pw[i] = -power[i] * 1000 * float(tm.fast_charge_limit_w(np.array([plant.soc[i]]))[0])
                elif plant.soc[i] > 0.1:
                    pw[i] = sc.drive_power_w(mode[i], t)
            cur = tm.current_for_power(pw, plant.soc)
            twin.soc = plant.soc.copy()
            twin.r_abs = plant.resistance() * (1 + rng.normal(0, 0.02, plant.t_core.shape))
            plant.step(2.0, cur, amb, cool_p, chiller_set_c=set_c)
            twin.step(2.0, cur, amb, cool_t, t_coolant_in_meas=plant.t_coolant_in, update_soc=False)
            if k > 150 and k % 10 == 0:
                snaps.append((plant.t_surf - twin.t_surf).reshape(n, -1))
    r = np.concatenate(snaps)
    cov = r.T @ r / len(r)
    scale = float(np.mean(np.diag(cov)))
    cov_n = cov / scale + 1e-3 * np.eye(tm.N_CELLS)
    MODEL_DIR.mkdir(exist_ok=True)
    np.savez(MODEL_DIR / "spatial_cov.npz", cov=cov_n, scale_k2=scale)
    log("cov: samples", len(r), "rms residual K", round(float(np.sqrt(scale)), 3))
    return cov_n


def placement_curves(cov: np.ndarray) -> dict:
    scale = float(np.load(MODEL_DIR / "spatial_cov.npz")["scale_k2"])
    rng = np.random.default_rng(5)
    greedy_full = est.greedy_layout(cov, max(N_COUNTS))
    out = {"counts": N_COUNTS, "greedy": [], "uniform": [], "random": [], "prior_rmse": float(np.sqrt(scale)),
           "layouts": {"greedy": {}, "uniform": {}}}
    for n in N_COUNTS:
        g = greedy_full[:n]
        u = est.uniform_layout(n)
        out["greedy"].append(est.expected_rmse(cov, g) * np.sqrt(scale))
        out["uniform"].append(est.expected_rmse(cov, u) * np.sqrt(scale))
        out["random"].append(float(np.mean([est.expected_rmse(cov, est.random_layout(n, rng)) for _ in range(8)])) * np.sqrt(scale))
        out["layouts"]["greedy"][str(n)] = [est.index_cell(i) for i in g]
        out["layouts"]["uniform"][str(n)] = [est.index_cell(i) for i in u]
    return out


# ─────────────────────────────────────────────
def run_closed_loop(probe_idx, use_voltage, scenario: dict, seconds=1500, sample_dt=4.0) -> dict:
    """시뮬레이션 차량 → 텔레메트리 → 트윈. 실제 코어/표면 온도와 비교."""
    v = SimVehicle(seed=scenario["seed"])
    for k, val in scenario.items():
        if hasattr(v.ctrl, k):
            setattr(v.ctrl, k, val)
    v.reset()
    tw = TwinEstimator(v.ctrl.channel_width_mm, v.ctrl.channel_depth_mm, v.ctrl.coolant_flow_lpm, use_voltage=use_voltage)
    errs_c, errs_s, errs_max = [], [], []
    steps = int(seconds / sample_dt)
    for k in range(steps):
        for _ in range(int(sample_dt)):
            v.step(1.0)
        out = tw.step(v.telemetry(probe_idx))
        if k * sample_dt > 300:
            tr = v.truth()
            errs_c.append(out["core_pinn"] - tr["core"])
            errs_s.append(out["surf"] - tr["surf"])
            errs_max.append(out["core_pinn"].max() - tr["core"].max())
    ec, es = np.array(errs_c), np.array(errs_s)
    return {"core_rmse": float(np.sqrt(np.mean(ec**2))), "surf_rmse": float(np.sqrt(np.mean(es**2))),
            "core_max_err": float(np.abs(ec).max()), "peak_err": float(np.sqrt(np.mean(np.array(errs_max) ** 2)))}


SCENARIOS = [
    {"seed": 21, "mode": "fast_charge", "charge_power_kw": 180, "ambient_c": 30, "coolant_flow_lpm": 8, "true_soh": 0.8},
    {"seed": 22, "mode": "fast_charge", "charge_power_kw": 120, "ambient_c": 10, "coolant_flow_lpm": 14, "true_soh": 0.92},
    {"seed": 23, "mode": "aggressive", "ambient_c": 35, "coolant_flow_lpm": 6, "true_soh": 0.75},
]


def validate_sim(placement: dict) -> dict:
    cov = est.load_spatial_cov()
    greedy_full = est.greedy_layout(cov, 32)
    res = {"counts": [4, 8, 16, 32], "with_voltage": {"greedy": [], "uniform": []}, "temp_only": {"uniform": []}}
    for n in res["counts"]:
        for name, layout in (("greedy", greedy_full[:n]), ("uniform", est.uniform_layout(n))):
            r = [run_closed_loop(layout, True, s) for s in SCENARIOS]
            res["with_voltage"][name].append({k: float(np.mean([x[k] for x in r])) for k in r[0]})
        r = [run_closed_loop(est.uniform_layout(n), False, s) for s in SCENARIOS]
        res["temp_only"]["uniform"].append({k: float(np.mean([x[k] for x in r])) for k in r[0]})
        log("sim validate n", n, res["with_voltage"]["greedy"][-1]["core_rmse"], res["with_voltage"]["uniform"][-1]["core_rmse"],
            res["temp_only"]["uniform"][-1]["core_rmse"])
    return res


# ─────────────────────────────────────────────
def load_real(name: str):
    import csv
    meta = json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
    with open(DATA_DIR / f"{name}.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return meta, rows


def real_sample(row: dict, meta: dict, use_probes: list[int]) -> dict:
    probes = [float(row[f"tp_{k + 1:02d}"]) for k in range(len(meta["probe_cells"]))]
    cells = [est.cell_index(*meta["probe_cells"][k]) for k in use_probes]
    return {"t_s": float(row["t_s"]), "current_a": float(row["current_a"]), "soc": float(row["soc_pct"]) / 100,
            "v_cells": [float(row[f"v_{i:02d}"]) for i in range(1, 97)],
            "probe_idx": cells, "probe_t": [probes[k] for k in use_probes]}


def real_probe_arrays(names):
    out = []
    for n in names:
        meta, rows = load_real(n)
        out.append(np.array([[float(r[f"tp_{k + 1:02d}"]) for k in range(len(meta["probe_cells"]))] for r in rows]))
    return out


def real_cov(names) -> np.ndarray:
    meta, _ = load_real(names[0])
    return est.expand_probe_cov(est.probe_covariance(real_probe_arrays(names)), meta["probe_cells"])


def real_holdout(name: str, n_use: int, use_voltage: bool = True, flow: float = 30.0, cov_from=None) -> dict:
    """실차 검증: 프로브 n_use 개만 트윈에 주고, 나머지 프로브 온도를 맞히는지 확인.
    공간 공분산은 '다른' 구간들에서만 학습 (교차검증)"""
    meta, rows = load_real(name)
    others = cov_from if cov_from is not None else [f.stem for f in DATA_DIR.glob("*.json") if f.stem != name]
    n_probe = len(meta["probe_cells"])
    use = sorted(set(int(round(x)) for x in np.linspace(0, n_probe - 1, n_use))) if n_use < n_probe else list(range(n_probe))
    hold = [k for k in range(n_probe) if k not in use]
    tw = TwinEstimator(flow_lpm=flow, sensor_noise=0.45, use_voltage=use_voltage, ocv_known=False, k_prior=1.6,
                       gp_cov=real_cov(others) if others else None)
    errs, base, nearest = [], [], []
    for j, row in enumerate(rows):
        out = tw.step(real_sample(row, meta, use))
        if j > 30 and hold:
            truth = np.array([float(row[f"tp_{k + 1:02d}"]) for k in hold])
            cells = [est.cell_index(*meta["probe_cells"][k]) for k in hold]
            errs.append(out["surf"][cells] - truth)
            used_t = np.array([float(row[f"tp_{k + 1:02d}"]) for k in use])
            base.append(np.full(len(hold), used_t.mean()) - truth)    # 단순 평균 대체 기준선
            pc = np.array(meta["probe_cells"])
            near = [use[int(np.argmin(np.abs(pc[use, 0] - pc[k, 0]) * 100 + np.abs(pc[use, 1] - pc[k, 1])))] for k in hold]
            nearest.append(np.array([float(row[f"tp_{k + 1:02d}"]) for k in near]) - truth)  # 가장 가까운 프로브 기준선
    e, b = np.array(errs), np.array(base)
    return {"n_use": len(use), "n_holdout": len(hold), "rmse": float(np.sqrt(np.mean(e**2))) if len(e) else None,
            "max_err": float(np.abs(e).max()) if len(e) else None,
            "mean_baseline_rmse": float(np.sqrt(np.mean(b**2))) if len(b) else None,
            "nearest_baseline_rmse": float(np.sqrt(np.mean(np.array(nearest) ** 2))) if nearest else None,
            "k_cells_spread": float(np.std(tw.rls.k_cells)), "k_mean": float(tw.rls.k_mean),
            "weak_cells": [est.index_cell(int(i)) for i in np.argsort(-tw.rls.k_cells)[:3]]}


def validate_real() -> dict:
    out = {}
    for f in sorted(DATA_DIR.glob("*.json")):
        name = f.stem
        out[name] = {"title": json.loads(f.read_text(encoding="utf-8"))["title"], "by_n": []}
        for n in (4, 8, 16, 24):
            r = real_holdout(name, n)
            out[name]["by_n"].append(r)
            log("real", name, n, r["rmse"], r["mean_baseline_rmse"])
    return out


def main(argv):
    what = argv[1] if len(argv) > 1 else "all"
    MODEL_DIR.mkdir(exist_ok=True)
    if what in ("all", "pinn"):
        build_pinn()
    if what in ("all", "cov"):
        cov = build_cov()
        pl = placement_curves(cov)
        (MODEL_DIR / "placement.json").write_text(json.dumps(pl), encoding="utf-8")
    if what in ("all", "validate"):
        pl = json.loads((MODEL_DIR / "placement.json").read_text(encoding="utf-8"))
        pl["sim_validation"] = validate_sim(pl)
        (MODEL_DIR / "placement.json").write_text(json.dumps(pl), encoding="utf-8")
        if DATA_DIR.exists():
            (MODEL_DIR / "real_validation.json").write_text(json.dumps(validate_real(), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
