"""
PINN 재보정 — 코어 열전대를 심은 '셀 시험' 데이터로 코어온도 가상센서를 다시 학습합니다.
(남은 문제 1: 실차 코어온도 참값이 없어 PINN 이 시뮬레이션 라벨로만 학습된 문제를 실제 데이터로 바꾸는 경로)

[입력 CSV] 시험 1개 또는 여러 개 (파일 여러 개, 또는 test_id 열로 구분)
  필수: t_s, current_a (+방전 / −충전, 셀 전류), t_surf_c (표면 열전대), t_core_c (코어 열전대)
  발열: q_w 열이 있으면 그대로, 없으면 v_cell + soc 로 계산  q = I·(OCV − V) − I·T·dU/dT
  선택: soc (0~1 또는 %), soh (기본 1.0), test_id
  셀 사양이 다르면 thermal_model.py 의 OCV 표·용량을 그 셀 값으로 바꾼 뒤 실행하세요.

[실행]
  backend\\.venv\\Scripts\\python.exe backend\\calibrate_pinn.py data\\cell_test\\*.csv            → 비교 보고서만
  backend\\.venv\\Scripts\\python.exe backend\\calibrate_pinn.py data\\cell_test\\*.csv --apply    → 더 정확하면 교체
  backend\\.venv\\Scripts\\python.exe backend\\calibrate_pinn.py --make-example data\\cell_test     → 예제 시험 데이터 생성

[방법] ① 코어 열전대가 있으므로 열전달 식의 R_in·C 를 구간 적분 최소제곱으로 직접 식별
       ② R_in·C 를 그 값으로 고정하고 PINN 신경망을 데이터 손실 + 물리 손실로 학습
[검증] 마지막 시험(시험이 1개면 뒤쪽 30 %)은 학습에 쓰지 않고, 현재 모델과 새 모델의 코어 오차를 비교합니다.
       --apply 는 새 하이브리드(학습 R_in·C + 물리식)가 현재보다 정확할 때만 models/pinn.npz 를 바꾸고, 이전 파일은 백업합니다.
"""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

import pinn
import thermal_model as tm
from paths import MODEL_DIR
REQUIRED = ("t_s", "current_a", "t_surf_c", "t_core_c")


# ─────────────────────────────────────────────
# 데이터 읽기 → 앙상블 형식 (pinn.make_pairs 와 같은 모양, 셀 1개 = (T, 1, 1, 1))
# ─────────────────────────────────────────────
def read_tests(paths: list[str]) -> list[dict]:
    import pandas as pd  # noqa: PLC0415
    tests = []
    for p in paths:
        df = pd.read_csv(p)
        missing = [c for c in REQUIRED if c not in df.columns]
        if missing:
            raise ValueError(f"{p}: 필수 열이 없습니다 {missing}")
        if "q_w" not in df.columns and not {"v_cell", "soc"} <= set(df.columns):
            raise ValueError(f"{p}: q_w 열 또는 v_cell + soc 열이 필요합니다")
        groups = df.groupby("test_id", sort=False) if "test_id" in df.columns else [(Path(p).stem, df)]
        for tid, g in groups:
            tests.append({"name": f"{Path(p).stem}:{tid}" if "test_id" in df.columns else str(tid), "df": g.sort_values("t_s")})
    if not tests:
        raise ValueError("시험 데이터가 없습니다")
    return tests


def to_ensemble(df, dt: float) -> dict:
    t = df["t_s"].to_numpy(float)
    grid = np.arange(t[0], t[-1], dt)
    if len(grid) < int(900 / dt):
        raise ValueError("시험 하나가 15분 이상이어야 합니다 (이동평균 특징이 자리잡는 시간)")

    def col(name, default=None):
        if name not in df.columns:
            return None if default is None else np.full(len(grid), default)
        return np.interp(grid, t, df[name].to_numpy(float))

    current = col("current_a")
    soc = col("soc", 0.5)
    soc = soc / 100.0 if np.nanmax(soc) > 1.0 else soc
    ts, tc = col("t_surf_c"), col("t_core_c")
    q = col("q_w")
    if q is None:
        v = col("v_cell")
        q = current * (tm.ocv(soc) - v) - current * (tc + 273.15) * tm.dudt(soc)
    soh = float(df["soh"].iloc[0]) if "soh" in df.columns else 1.0
    r = lambda a: a.reshape(-1, 1, 1, 1)  # noqa: E731
    return {"ts": r(ts), "tc": r(tc), "q": r(q), "soc": soc.reshape(-1, 1), "current": current.reshape(-1, 1),
            "soh": np.array([soh]), "dt_sample": dt}


def slice_ens(ens: dict, sl: slice) -> dict:
    out = dict(ens)
    for k in ("ts", "tc", "q", "soc", "current"):
        out[k] = ens[k][sl]
    return out


def identify_rc(enss: list[dict], window_s: float = 120.0) -> tuple[float, float]:
    """코어 열전대가 있으므로 열전달 식을 구간 적분한 최소제곱으로 C, R_in 을 직접 구함
         ∫q dt = C·ΔTc + (1/R_in)·∫(Tc − Ts) dt      (미분 대신 적분 → 열전대 잡음에 강함)"""
    rows, rhs = [], []
    for e in enss:
        dt = float(e["dt_sample"])
        w = max(2, int(window_s / dt))
        tc, ts, q = e["tc"].ravel(), e["ts"].ravel(), e["q"].ravel()
        cq = np.concatenate([[0.0], np.cumsum(0.5 * (q[1:] + q[:-1]) * dt)])
        g = tc - ts
        cg = np.concatenate([[0.0], np.cumsum(0.5 * (g[1:] + g[:-1]) * dt)])
        for k in range(0, len(tc) - w, max(1, w // 2)):
            rows.append([tc[k + w] - tc[k], cg[k + w] - cg[k]])
            rhs.append(cq[k + w] - cq[k])
    a, b = np.array(rows), np.array(rhs)
    (c, inv_r), *_ = np.linalg.lstsq(a, b, rcond=None)
    if not (c > 0 and inv_r > 0):
        raise ValueError("C·R_in 식별 실패 (전류 변화가 있는 시험 구간이 더 필요합니다)")
    return float(1.0 / inv_r), float(c)


def pairs_from(enss: list[dict], rng) -> dict:
    parts = [pinn.make_pairs(e, lag=3, rng=rng, frac=1.0) for e in enss]
    out = {k: np.concatenate([p[k] for p in parts]) for k in parts[0] if k != "dt"}
    out["dt"] = parts[0]["dt"]
    return out


# ─────────────────────────────────────────────
# 평가
# ─────────────────────────────────────────────
def core_errors(net: pinn.MLP | None, r_in: float, c: float, ens: dict) -> dict:
    dt = float(ens["dt_sample"])
    warm = int(300 / dt)
    tc = ens["tc"][warm:]
    hyb = pinn.physics_core_rollout(ens["ts"], ens["q"], dt, r_in, c)[warm:]
    out = {"hybrid_rmse": float(np.sqrt(np.mean((hyb - tc) ** 2))), "hybrid_max": float(np.abs(hyb - tc).max())}
    if net is not None:
        e = pinn.evaluate(net, ens)
        out.update({"nn_rmse": e["pinn_rmse"], "nn_max": e["pinn_max"],
                    "datasheet_rmse": e["phys_rmse"], "surface_as_core_rmse": e["surface_only_rmse"]})
    return out


def calibrate(paths: list[str], dt: float, iters: int, apply: bool, seed: int = 0) -> dict:
    t0 = time.time()
    tests = read_tests(paths)
    enss = [to_ensemble(t["df"], dt) for t in tests]
    if len(enss) >= 2:
        train_e, test_e = enss[:-1], [enss[-1]]
        split = f"시험 {len(enss) - 1}개 학습 · 마지막 시험({tests[-1]['name']}) 검증"
    else:
        n = enss[0]["ts"].shape[0]
        cut = int(n * 0.7)
        train_e, test_e = [slice_ens(enss[0], slice(0, cut))], [enss[0]]
        split = "시험 1개: 앞 70 % 학습 · 전체 구간 평가 (뒤 30 % 는 처음 보는 구간)"

    current = pinn.PINNVirtualSensor()
    r_ls, c_ls = identify_rc(train_e)                       # ① 물리 파라미터 직접 식별
    rng = np.random.default_rng(seed)
    pairs = pairs_from(train_e, rng)
    labeled = np.ones(len(pairs["b"]), dtype=bool)          # 셀 시험은 모든 샘플에 코어 열전대 라벨이 있음
    # ② R_in·C 를 식별값으로 고정하고 신경망을 데이터 + 물리 손실로 학습
    #    (라벨이 충분한 셀 시험에서 R_in·C 까지 같이 풀면 신경망 오차를 C 가 흡수해 엉뚱한 값으로 흘러감)
    net, phys, hist = pinn.train(pairs, labeled, use_physics=True, iters=iters, seed=seed,
                                 r_in0=r_ls, c0=c_ls, fit_phys=False)

    report_eval = []
    for e, name in zip(test_e, [tests[-1]["name"]] if len(enss) >= 2 else [tests[0]["name"]]):
        report_eval.append({
            "test": name,
            "datasheet": core_errors(None, tm.R_IN, tm.C_CORE, e),
            "current_model": core_errors(current.net if current.ok else None, current.R_in, current.C, e),
            "new_model": core_errors(net, phys["R_in"], phys["C"], e),
        })
    old = np.mean([r["current_model"]["hybrid_rmse"] for r in report_eval])
    new = np.mean([r["new_model"]["hybrid_rmse"] for r in report_eval])
    # 신경망은 따로 비교: 시험 데이터가 적어 새 신경망이 더 나쁘면 기존 신경망을 유지
    # (위험 판정은 하이브리드와 신경망 중 높은 값을 쓰므로, 나쁜 신경망은 오경보를 늘림)
    old_nn = [r["current_model"].get("nn_rmse") for r in report_eval]
    new_nn = np.mean([r["new_model"]["nn_rmse"] for r in report_eval])
    keep_old_nn = current.ok and None not in old_nn and float(np.mean(old_nn)) <= new_nn
    report = {
        "calibrated_at": time.strftime("%Y-%m-%d %H:%M"), "files": [str(p) for p in paths],
        "tests": [t["name"] for t in tests], "split": split, "dt_s": dt, "iters": iters, "pairs": int(len(labeled)),
        "params": {"R_in_current": current.R_in, "C_current": current.C, "R_in_new": phys["R_in"], "C_new": phys["C"],
                   "R_in_least_squares": r_ls, "C_least_squares": c_ls,
                   "R_in_datasheet": tm.R_IN, "C_datasheet": tm.C_CORE},
        "eval": report_eval, "improved": bool(new < old), "applied": False,
        "neural_net": "기존 유지 (새 신경망이 검증에서 더 부정확)" if keep_old_nn else "새로 학습한 신경망",
        "seconds": round(time.time() - t0, 1), "history": hist[-10:],
    }
    if apply and new < old:
        target = MODEL_DIR / "pinn.npz"
        if target.exists():
            stamp = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(target, MODEL_DIR / f"pinn_before_calibration_{stamp}.npz")
            shutil.copy2(target.with_suffix(".json"), MODEL_DIR / f"pinn_before_calibration_{stamp}.json")
        base = dict(current.report)
        base["calibration"] = report
        report["applied"] = True
        pinn.save(current.net if keep_old_nn else net, phys, base, target)
    (MODEL_DIR / "pinn_calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


# ─────────────────────────────────────────────
# 예제 셀 시험 데이터 (실제 시험이 없을 때 파이프라인 확인용 — 진짜 시험 데이터가 아님)
# ─────────────────────────────────────────────
def make_example(out_dir: Path, r_in_true: float = 0.17, c_true: float = 2100.0, seed: int = 5) -> list[Path]:
    """코어·표면 2노드 셀 + 항온 냉각판 시험대. R_in·C '참값'을 데이터시트·현재 모델과 다르게 두어 재보정 효과를 확인"""
    rng = np.random.default_rng(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = tm.CELL_CAPACITY_AH
    c_s, r_out, t_bath = tm.C_SURF, 0.25, 25.0
    profiles = {
        "fastcharge_2C": lambda t: -2.0 * cap if t < 1500 else 0.0,
        "drive_pulses": lambda t: cap * (1.5 * np.sin(t / 37.0) ** 2 + 1.2 * (np.sin(t / 11.0) > 0.6)) if t < 2400 else 0.0,
        "step_1C_3C": lambda t: (-1.0 if t < 900 else -3.0 if t < 1500 else 0.0) * cap,
    }
    paths = []
    for name, prof in profiles.items():
        dt, T = 1.0, 3000
        tc = ts = t_bath
        soc = 0.15 if prof(0) < 0 else 0.9
        rows = []
        for k in range(T):
            t = k * dt
            i = float(prof(t))
            r = float(tm.r_base(np.array(tc), np.array(soc)))
            v = float(tm.ocv(np.array(soc))) - i * r
            q = i * i * r - i * (tc + 273.15) * float(tm.dudt(np.array(soc)))
            tc, ts = tc + dt * (q - (tc - ts) / r_in_true) / c_true, ts + dt * ((tc - ts) / r_in_true - (ts - t_bath) / r_out) / c_s
            soc = float(np.clip(soc - i * dt / 3600.0 / cap, 0.0, 1.0))
            rows.append((t, i, soc, v + rng.normal(0, 0.001), ts + rng.normal(0, 0.1), tc + rng.normal(0, 0.1)))
        p = out_dir / f"example_{name}.csv"
        with open(p, "w", encoding="utf-8") as f:
            f.write("t_s,current_a,soc,v_cell,t_surf_c,t_core_c\n")
            for row in rows:
                f.write(",".join(f"{x:.4f}" for x in row) + "\n")
        paths.append(p)
    (out_dir / "README.txt").write_text(
        "example_*.csv 는 calibrate_pinn.py 를 확인하기 위한 합성 데이터입니다 (실제 셀 시험이 아님).\n"
        f"참값: R_in = {r_in_true} K/W, C = {c_true} J/K. 실제 코어 열전대 시험 데이터를 같은 열 형식으로 넣어 쓰세요.\n",
        encoding="utf-8")
    return paths


def main(argv: list[str]) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="셀 시험(코어 열전대) 데이터로 PINN 재보정")
    ap.add_argument("csv", nargs="*", help="시험 CSV (와일드카드 가능)")
    ap.add_argument("--apply", action="store_true", help="새 모델이 더 정확하면 models/pinn.npz 교체 (이전 파일 백업)")
    ap.add_argument("--dt", type=float, default=2.0, help="재표본 간격 [s]")
    ap.add_argument("--iters", type=int, default=3000)
    ap.add_argument("--make-example", metavar="DIR", help="예제 셀 시험 CSV 를 DIR 에 생성")
    a = ap.parse_args(argv)
    if a.make_example:
        for p in make_example(Path(a.make_example)):
            print("생성:", p)
        return
    paths = sorted({p for pat in a.csv for p in (glob.glob(pat) or [pat])})
    if not paths:
        ap.error("시험 CSV 를 지정하세요 (또는 --make-example)")
    rep = calibrate(paths, a.dt, a.iters, a.apply)
    print(f"[{rep['split']}]  학습 쌍 {rep['pairs']}개, {rep['seconds']} s")
    p = rep["params"]
    print(f"R_in: 데이터시트 {p['R_in_datasheet']:.4f} · 현재 {p['R_in_current']:.4f} → 새 {p['R_in_new']:.4f} K/W (시험 데이터 최소제곱)")
    print(f"C   : 데이터시트 {p['C_datasheet']:.0f} · 현재 {p['C_current']:.0f} → 새 {p['C_new']:.0f} J/K")
    for r in rep["eval"]:
        print(f"검증 '{r['test']}' 코어 RMSE [K]:")
        print(f"  데이터시트 물리       {r['datasheet']['hybrid_rmse']:.3f}")
        cm, nm = r["current_model"], r["new_model"]
        print(f"  현재 모델  하이브리드 {cm['hybrid_rmse']:.3f}" + (f" · 신경망 {cm['nn_rmse']:.3f}" if "nn_rmse" in cm else ""))
        print(f"  새 모델    하이브리드 {nm['hybrid_rmse']:.3f} · 신경망 {nm['nn_rmse']:.3f}")
    print("신경망:", rep["neural_net"])
    print("결과:", "적용함 (이전 모델 백업)" if rep["applied"] else ("개선됨 — --apply 로 적용 가능" if rep["improved"] else "개선 없음 — 기존 모델 유지"))
    print("보고서: backend/models/pinn_calibration.json")


if __name__ == "__main__":
    main(sys.argv[1:])
