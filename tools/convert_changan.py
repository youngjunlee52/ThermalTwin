"""
실차 데이터 변환기 — 300대 EV 공개 데이터 (Liu et al., Nat. Commun. 16, 1137, 2025, MIT License)

원본: https://github.com/HoraceLiu1010/Multi-modal-SOH-estimation-framework (EV_feild_data_sample/data_*.rar)
전체 데이터: http://ivstskl.changan.com.cn/?p=2697

원본 열: terminaltime(s), soc(%), speed, chargestatus(1=충전, 3=주행), totalvoltage, totalcurrent(+방전),
        batteryvoltage(셀 96개 '~' 구분), probetemperatures(프로브 32개 '~' 구분, 1 °C 분해능)

ThermalTwin 표준 텔레메트리 CSV 로 변환:
  t_s, current_a(+방전), soc_pct, speed_kmh, pack_voltage_v, charge_status, v_01..v_96, tp_01..tp_32

사용법 (rar 압축을 푼 data_1.csv 기준):
  python tools/convert_changan.py data_1.csv data/real_ev            # 긴 충전·주행 구간 자동 추출
  python tools/convert_changan.py data_1.csv data/real_ev --all      # 연속 구간 전부
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

N_CELLS, N_PROBES = 96, 32
# 프로브 32개의 실제 위치는 공개되지 않음 → 8모듈 × 4개(열 1, 4, 7, 10)로 균등 배치했다고 가정
PROBE_CELLS = [[k // 4, [1, 4, 7, 10][k % 4]] for k in range(N_PROBES)]

CITATION = ("Liu, H., Li, C., Hu, X. et al. Multi-modal framework for battery state of health evaluation "
            "using open-source electric vehicle data. Nat Commun 16, 1137 (2025). "
            "https://doi.org/10.1038/s41467-025-56485-7")


def load_raw(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["chargestatus"].isin([1, 3]) & (df["totalvoltage"] > 100)].copy()
    v = df["batteryvoltage"].astype(str).str.split("~", expand=True)
    p = df["probetemperatures"].astype(str).str.split("~", expand=True)
    ok = (v.shape[1] >= N_CELLS) and (p.shape[1] >= N_PROBES)
    if not ok:
        raise SystemExit("셀 전압 96개 / 프로브 32개 형식이 아닙니다.")
    out = pd.DataFrame({
        "t_s": df["terminaltime"].astype(float).values,
        "current_a": df["totalcurrent"].astype(float).values,
        "soc_pct": df["soc"].astype(float).values,
        "speed_kmh": df["speed"].astype(float).values,
        "pack_voltage_v": df["totalvoltage"].astype(float).values,
        "charge_status": df["chargestatus"].astype(int).values,
    })
    for i in range(N_CELLS):
        out[f"v_{i + 1:02d}"] = pd.to_numeric(v[i], errors="coerce").values
    for i in range(N_PROBES):
        out[f"tp_{i + 1:02d}"] = pd.to_numeric(p[i], errors="coerce").values
    out = out.dropna()
    # 비정상값 제거 (센서 오류 코드 -40 °C 등)
    tp = out[[f"tp_{i + 1:02d}" for i in range(N_PROBES)]]
    out = out[(tp > -30).all(axis=1) & (tp < 80).all(axis=1)]
    return out.reset_index(drop=True)


def segments(df: pd.DataFrame, max_gap_s: float = 30.0) -> list[pd.DataFrame]:
    t = df["t_s"].values
    cs = df["charge_status"].values
    brk = np.concatenate([[True], (np.diff(t) > max_gap_s) | (np.diff(cs) != 0)])
    ids = np.cumsum(brk)
    return [g for _, g in df.groupby(ids)]


def save(seg: pd.DataFrame, out_dir: Path, name: str, title: str) -> dict:
    seg = seg.copy()
    seg["t_s"] = seg["t_s"] - seg["t_s"].iloc[0]
    csv_path = out_dir / f"{name}.csv"
    seg.to_csv(csv_path, index=False, float_format="%.4g")
    tp = seg[[f"tp_{i + 1:02d}" for i in range(N_PROBES)]].values
    meta = {
        "name": name, "title": title, "rows": int(len(seg)),
        "duration_min": round(float(seg["t_s"].iloc[-1]) / 60, 1),
        "soc_start": float(seg["soc_pct"].iloc[0]), "soc_end": float(seg["soc_pct"].iloc[-1]),
        "current_mean_a": round(float(seg["current_a"].mean()), 1),
        "probe_max_start_c": float(tp[0].max()), "probe_max_peak_c": float(tp.max()),
        "probe_cells": PROBE_CELLS, "probe_position_assumed": True,
        "source": "300-EV field data (Changan), 96S NCM 155 Ah", "license": "MIT", "citation": CITATION,
    }
    (out_dir / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_csv")
    ap.add_argument("out_dir")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_raw(Path(a.raw_csv))
    segs = [s for s in segments(df) if len(s) >= 120]
    print(f"{len(df)} rows, {len(segs)} segments (>= 20 min)")
    if a.all:
        for k, s in enumerate(segs):
            kind = "charge" if s["charge_status"].iloc[0] == 1 else "drive"
            print(save(s, out_dir, f"ev_{kind}_{k:03d}", f"{kind} {k}"))
        return
    charges = sorted([s for s in segs if s["charge_status"].iloc[0] == 1], key=lambda s: s["current_a"].mean())
    drives = sorted([s for s in segs if s["charge_status"].iloc[0] == 3], key=lambda s: -len(s))
    picks = []
    if charges:
        # 가장 강한 충전 + 차가운 상태에서 시작한 긴 충전
        picks.append((charges[0], "ev_charge_strong", "실차 충전 — 평균 전류가 가장 큰 구간"))
        long_cold = sorted(charges[:15], key=lambda s: s.iloc[0][[f"tp_{i + 1:02d}" for i in range(N_PROBES)]].max())
        if long_cold and long_cold[0] is not charges[0]:
            picks.append((long_cold[0], "ev_charge_cold", "실차 충전 — 낮은 온도에서 시작"))
    if drives:
        picks.append((drives[0], "ev_drive_long", "실차 주행 — 가장 긴 연속 주행"))
    for seg, name, title in picks:
        print(save(seg, out_dir, name, title))


if __name__ == "__main__":
    sys.exit(main())
