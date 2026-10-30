"""
브라우저 데모용 백엔드 — main.py 와 같은 엔진을 Pyodide(브라우저 속 Python)에서 그대로 돌립니다.

설치 없이 GitHub Pages 주소만 열면 동작하도록, 웹 서버(FastAPI) 대신 이 모듈이
웹소켓 메시지와 /api 요청을 받아 처리합니다. 계산 코드는 설치판과 한 줄도 다르지 않습니다.
(외부 차량 실시간 연결·CSV 업로드·영구 로그 DB 는 설치판 전용)
"""
from __future__ import annotations

import json
import os

os.environ.setdefault("THERMALTWIN_DB", "off")

import estimator as est  # noqa: E402
from design_sweep import run_design_sweep  # noqa: E402
from engine import list_datasets  # noqa: E402
from fleet import DEFAULT_ID, Fleet  # noqa: E402
from paths import MODEL_DIR  # noqa: E402

fleet = Fleet(None)
engine = fleet.main
SECURITY = {"auth_required": False, "db_enabled": False, "web_demo": True}


def tick() -> str | None:
    """1초마다 호출 — 화면에 보낼 프레임(JSON)"""
    frame = engine.tick()
    if frame is None and engine.last is not None:
        frame = engine.frame()
    if frame is None:
        return None
    frame["fleet"] = {"view": fleet.view_id, "vehicles": fleet.summary()}
    frame["ingest"]["connected"] = 0
    frame["security"] = SECURITY
    return json.dumps(frame)


def on_message(raw: str) -> None:
    """화면 → 엔진 (main.py 의 ui_socket 과 같은 규칙)"""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    kind = msg.get("type")
    try:
        if kind in ("control", "reset") and msg.get("source") in ("sim", "replay", "ingest"):
            engine.user_source_hold = msg["source"] != "ingest"
        if kind == "control":
            engine.apply_control(msg)
        elif kind == "reset":
            engine.apply_control(msg)
            engine.reset()
            engine.running = True
        elif kind == "pause":
            engine.running = False
        elif kind == "resume":
            engine.running = True
        elif kind == "sensors":
            engine.set_sensors(msg.get("cells", []))
        elif kind == "view_vehicle":
            fleet.set_view(str(msg.get("vehicle_id", DEFAULT_ID)))
    except (ValueError, TypeError, KeyError, OSError) as e:
        print("잘못된 메시지", kind, e)


def _q(query: dict, key: str, default):
    v = query.get(key)
    return default if v is None else type(default)(v)


def api(method: str, path: str, query_json: str, body: str) -> str:
    """/api 요청 → [상태코드, 내용 형식, 본문] JSON"""
    q = json.loads(query_json or "{}")
    try:
        if path == "/api/health":
            out = {"service": "ThermalTwin", "status": "ok", "web_demo": True, "pinn_trained": engine.pinn.ok}
        elif path == "/api/datasets":
            out = [{k: d.get(k) for k in ("name", "title", "rows", "duration_min", "soc_start", "soc_end", "current_mean_a",
                                          "probe_max_start_c", "probe_max_peak_c", "source", "license", "citation")}
                   for d in list_datasets()]
        elif path == "/api/models":
            out = {}
            for key, fn in (("pinn", "pinn.json"), ("placement", "placement.json"), ("real_validation", "real_validation.json"),
                            ("soh_sensitivity", "soh_sensitivity.json"), ("robustness", "robustness.json")):
                f = MODEL_DIR / fn
                out[key] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        elif path == "/api/placement":
            n = max(1, min(96, _q(q, "n", 16)))
            cov = engine.sim_cov
            idx = est.greedy_layout(cov, n) if q.get("strategy", "greedy") == "greedy" else est.uniform_layout(n)
            out = {"n": n, "strategy": q.get("strategy", "greedy"), "cells": [est.index_cell(i) for i in idx],
                   "expected_rmse_norm": est.expected_rmse(cov, idx)}
        elif path == "/api/design-sweep" and method == "POST":
            r = {"charge_power_kw": 150, "ambient_c": 25, "coolant_set_c": 20, "soh": 0.85,
                 "channel_width_mm": 8, "channel_depth_mm": 3, "coolant_flow_lpm": 10, **json.loads(body or "{}")}
            current = (r["channel_width_mm"], r["channel_depth_mm"], r["coolant_flow_lpm"])
            out = run_design_sweep(r["charge_power_kw"], r["ambient_c"], r["coolant_set_c"], r["soh"], current)
        elif path == "/api/commands":
            e = fleet.get(q.get("vehicle", DEFAULT_ID)) or engine
            st = e.command_status()
            out = {"vehicle_id": q.get("vehicle", DEFAULT_ID), **e.commands, "valid": st["valid"], "stale": st["stale"],
                   "age_s": st["age_s"]}
        elif path == "/api/vehicles":
            out = {"view": fleet.view_id, "live": fleet.summary(), "stored": []}
        elif path == "/api/events":
            out = []
        elif path == "/api/log.csv":
            return json.dumps([200, "text/csv", fleet.view().log_csv()])
        elif path == "/api/history.csv":
            return json.dumps([200, "text/csv", fleet.view().log_csv()])
        elif path == "/api/datasets/upload":
            return json.dumps([400, "application/json", json.dumps(
                {"detail": "웹 데모에서는 CSV 업로드를 지원하지 않습니다. 설치판(1_setup → 2_start)을 사용하세요."},
                ensure_ascii=False)])
        else:
            return json.dumps([404, "application/json", json.dumps({"detail": "Not Found"})])
        return json.dumps([200, "application/json", json.dumps(out)])
    except Exception as e:  # noqa: BLE001
        return json.dumps([500, "application/json", json.dumps({"detail": str(e)[:300]}, ensure_ascii=False)])
