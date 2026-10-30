"""
ThermalTwin 백엔드 v0.4 — 전기차 배터리 열관리 디지털 트윈

[실행]  2_start.bat (또는: python -m uvicorn main:app --app-dir backend --port 8000)
[접속]  http://localhost:8000

[API]
  WS   /ws/battery-simulation   웹 화면 ↔ 엔진 (1초마다 상태, 설정·센서·소스 변경 수신)
  WS   /ws/ingest               외부 BMS/CAN 게이트웨이 → 텔레메트리 전송, 매번 제어 명령 응답
  POST /api/telemetry           위와 같은 텔레메트리 1건 (HTTP), 제어 명령 응답
       (THERMALTWIN_INGEST_TOKEN 설정 시 토큰 필요 · vehicle_id 로 여러 차량 · 타당성 검사 후 처리)
  GET  /api/commands?vehicle=…  최신 제어 명령 (충전 출력 상한·냉각수 유량·목표온도)
  GET  /api/vehicles            차량 목록 요약 · GET /api/events  입력 거부·경고 기록 (SQLite)
  GET  /api/datasets            실차 데이터 목록 · POST /api/datasets/upload?name=…  (CSV 본문)
  GET  /api/log.csv             화면에서 보고 있는 차량의 이번 실행 로그
  GET  /api/history.csv?vehicle=…&hours=…   영구 저장 로그 (logs/thermaltwin.db)
  GET  /api/models              PINN 학습 보고서 · 센서 배치 곡선 · 실차 검증 결과
  GET  /api/placement?n=16&strategy=greedy   최적/균등 센서 배치
  POST /api/design-sweep        냉각판 설계 탐색
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import estimator as est
import ingest_guard
from design_sweep import run_design_sweep
from engine import list_datasets
from fleet import DEFAULT_ID, EXTERNAL_ID, Fleet
from paths import FRONTEND_DIST, MODEL_DIR, TOOLS_DIR, UPLOAD_DIR
from store import LogStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("thermaltwin")

VERSION = "0.4.0"
app = FastAPI(title="ThermalTwin API", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

store = LogStore()
fleet = Fleet(store)
engine = fleet.main
ui_clients: set[WebSocket] = set()
lock = asyncio.Lock()


def security_info() -> dict:
    return {"auth_required": ingest_guard.auth_required(), "db_enabled": store.enabled}


@app.on_event("startup")
async def start_loop() -> None:
    if not ingest_guard.auth_required():
        logger.warning("THERMALTWIN_INGEST_TOKEN 이 없어 외부 입력(/ws/ingest, /api/telemetry)을 인증 없이 받습니다. "
                       "다른 기기에서 접속하게 할 때는 토큰을 설정하세요.")

    async def loop():
        while True:
            try:
                async with lock:
                    frame = await asyncio.to_thread(engine.tick)
                    view = fleet.view()
                    if view is not engine:
                        frame = view.frame() if view.last is not None else None
                    elif frame is None and engine.last is not None and ui_clients:
                        frame = engine.frame()       # 일시정지·외부입력 대기 중에도 화면 갱신
                    if frame is not None:
                        frame["fleet"] = {"view": fleet.view_id, "vehicles": fleet.summary()}
                        frame["ingest"]["connected"] = fleet.gateways
                        frame["security"] = security_info()
                if frame is not None:
                    msg = json.dumps(frame)
                    for ws in list(ui_clients):
                        try:
                            await ws.send_text(msg)
                        except Exception:  # noqa: BLE001
                            ui_clients.discard(ws)
            except Exception as e:  # noqa: BLE001
                logger.exception("엔진 오류: %s", e)
            await asyncio.sleep(1.0)
    asyncio.create_task(loop())


@app.on_event("shutdown")
async def close_store() -> None:
    store.close()


@app.get("/api/health")
async def health() -> dict:
    return {"service": "ThermalTwin", "version": VERSION, "status": "ok", "frontend_built": FRONTEND_DIST.exists(),
            "pinn_trained": engine.pinn.ok, "source": engine.ctrl.source, "vehicles": 1 + len(fleet.others),
            **security_info()}


# ── 웹 화면 ↔ 엔진 ──
@app.websocket("/ws/battery-simulation")
async def ui_socket(ws: WebSocket) -> None:
    await ws.accept()
    ui_clients.add(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            async with lock:
                try:
                    if kind in ("control", "reset") and msg.get("source") in ("sim", "replay", "ingest"):
                        # 화면에서 소스를 직접 고름 → 그 선택을 유지 (외부 데이터가 가로채지 않음)
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
                    logger.warning("잘못된 메시지 %s: %s", kind, e)
    except WebSocketDisconnect:
        pass
    finally:
        ui_clients.discard(ws)


# ── 외부 실시간 입력 (BMS·CAN 게이트웨이) ──
class Telemetry(BaseModel):
    vehicle_id: str = Field(default=DEFAULT_ID, description="차량 식별자 (영문·숫자·_- 1~32자). 없으면 화면 기본 차량")
    t_s: float | None = None
    current_a: float = Field(description="팩 전류 A (+ 방전, − 충전)")
    soc: float = Field(description="SOC (0~1 또는 %)")
    v_cells: list[float] | None = Field(default=None, description="셀 전압 96개 [V]")
    probe_cells: list[list[int]] = Field(default_factory=list, description="온도센서 위치 [[모듈 0~7, 셀 0~11], …]")
    probe_t: list[float] = Field(default_factory=list, description="온도센서 값 °C")
    t_coolant_in: float | None = None
    t_ambient: float | None = None
    coolant_flow_lpm: float | None = Field(default=None, description="실제 냉각수 유량 L/min (선택, 있으면 트윈 냉각 모델이 따라감)")


def ingest_one(data: dict) -> dict:
    """입력 우선순위:
       ① 기본 차량 앞으로 온 외부 실측 데이터는 기본 차량을 '외부 수신'으로 바꿔 처리 (실측 > 화면 설정)
       ② 단, 사용자가 화면에서 시뮬레이션·실차 재생을 직접 골라 둔 동안에는 그 선택을 유지하고,
          외부 데이터는 'external' 차량으로 따로 받음 (데이터는 버리지 않음)"""
    vid = data.get("vehicle_id") or DEFAULT_ID
    if not ingest_guard.VEHICLE_ID_RE.match(vid):
        raise ValueError("vehicle_id 는 영문·숫자·_- 1~32자여야 합니다")
    redirected = False
    if vid == DEFAULT_ID and engine.user_source_hold and engine.ctrl.source != "ingest":
        vid, redirected = EXTERNAL_ID, True
        engine.redirected_samples += 1
    data = {**data, "vehicle_id": vid}
    reply = fleet.for_ingest(vid).ingest(data)
    if redirected:
        reply["redirected_from"] = DEFAULT_ID
        reply["notice"] = "기본 차량은 화면에서 다른 소스로 고정돼 있어 'external' 차량으로 처리했습니다"
    return reply


@app.post("/api/telemetry")
async def post_telemetry(t: Telemetry, authorization: str | None = Header(default=None), token: str | None = None) -> dict:
    if not ingest_guard.check_token(authorization, token):
        raise HTTPException(401, "인증 토큰이 없거나 틀립니다")
    async with lock:
        try:
            return {"type": "commands", **ingest_one(t.model_dump())}
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e


@app.websocket("/ws/ingest")
async def ingest_socket(ws: WebSocket) -> None:
    if not ingest_guard.check_token(ws.headers.get("authorization"), ws.query_params.get("token")):
        logger.warning("게이트웨이 인증 실패: %s", ws.client)
        await ws.close(code=1008)
        return
    await ws.accept()
    fleet.gateways += 1
    logger.info("게이트웨이 연결됨: %s", ws.client)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = Telemetry(**json.loads(raw)).model_dump()
                async with lock:
                    cmd = ingest_one(data)
                await ws.send_json({"type": "commands", **cmd})
            except (ValueError, KeyError, TypeError) as e:
                await ws.send_json({"type": "error", "message": str(e)[:300]})
    except WebSocketDisconnect:
        pass
    finally:
        fleet.gateways = max(0, fleet.gateways - 1)
        logger.info("게이트웨이 연결 끊김")


@app.get("/api/commands")
async def get_commands(vehicle: str = DEFAULT_ID) -> dict:
    e = fleet.get(vehicle)
    if e is None:
        raise HTTPException(404, f"차량 {vehicle} 이 없습니다")
    st = e.command_status()
    return {"vehicle_id": vehicle, **e.commands, "valid": st["valid"], "stale": st["stale"], "age_s": st["age_s"]}


@app.get("/api/vehicles")
async def vehicles() -> dict:
    return {"view": fleet.view_id, "live": fleet.summary(), "stored": await asyncio.to_thread(store.vehicles)}


@app.get("/api/events")
async def events(vehicle: str | None = None, limit: int = 200) -> list[dict]:
    return await asyncio.to_thread(store.events, vehicle, max(1, min(2000, limit)))


# ── 데이터 ──
@app.get("/api/datasets")
async def datasets() -> list[dict]:
    return [{k: d.get(k) for k in ("name", "title", "rows", "duration_min", "soc_start", "soc_end", "current_mean_a",
                                   "probe_max_start_c", "probe_max_peak_c", "source", "license", "citation")}
            for d in list_datasets()]


@app.post("/api/datasets/upload")
async def upload_dataset(request: Request, name: str = "uploaded") -> dict:
    """표준 텔레메트리 CSV 또는 300대 EV 원본 CSV(자동 변환) 업로드"""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:40] or "uploaded"
    body = await request.body()
    if len(body) > 300 * 1024 * 1024:
        raise HTTPException(413, "파일이 너무 큽니다 (300 MB 이하)")
    head = body[:4000].decode("utf-8", errors="ignore")
    import sys
    sys.path.insert(0, str(TOOLS_DIR))
    import convert_changan as cc  # noqa: PLC0415
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.csv"
        p.write_bytes(body)
        if "batteryvoltage" in head and "probetemperatures" in head:
            df = cc.load_raw(p)
            segs = [s for s in cc.segments(df) if len(s) >= 60]
            if not segs:
                raise HTTPException(400, "20분 이상 연속 구간이 없습니다")
            seg = max(segs, key=len)
            meta = cc.save(seg, UPLOAD_DIR, f"user_{safe}", f"업로드: {name} (가장 긴 구간)")
        elif "current_a" in head and "v_01" in head and "tp_01" in head:
            import pandas as pd  # noqa: PLC0415
            seg = pd.read_csv(p)
            meta = cc.save(seg, UPLOAD_DIR, f"user_{safe}", f"업로드: {name}")
        else:
            raise HTTPException(400, "지원하지 않는 형식입니다. README 의 표준 텔레메트리 CSV 형식을 참고하세요.")
    return {"ok": True, "dataset": meta["name"], "rows": meta["rows"]}


@app.get("/api/log.csv")
async def log_csv() -> PlainTextResponse:
    return PlainTextResponse(fleet.view().log_csv(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=thermaltwin_log.csv"})


@app.get("/api/history.csv")
async def history_csv(vehicle: str | None = None, hours: float | None = None) -> PlainTextResponse:
    import time  # noqa: PLC0415
    since = time.time() - hours * 3600 if hours else None
    text = await asyncio.to_thread(store.csv, vehicle, since)
    name = f"thermaltwin_history_{vehicle or 'all'}.csv"
    return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={name}"})


# ── 모델 · 센서 배치 · 설계 ──
@app.get("/api/models")
async def models() -> dict:
    out = {}
    for key, fn in (("pinn", "pinn.json"), ("placement", "placement.json"), ("real_validation", "real_validation.json"),
                    ("soh_sensitivity", "soh_sensitivity.json"), ("robustness", "robustness.json")):
        f = MODEL_DIR / fn
        out[key] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    return out


@app.get("/api/placement")
async def placement(n: int = 16, strategy: str = "greedy") -> dict:
    n = max(1, min(96, n))
    cov = engine.sim_cov
    idx = est.greedy_layout(cov, n) if strategy == "greedy" else est.uniform_layout(n)
    return {"n": n, "strategy": strategy, "cells": [est.index_cell(i) for i in idx],
            "expected_rmse_norm": est.expected_rmse(cov, idx)}


class SweepRequest(BaseModel):
    charge_power_kw: float = Field(150, ge=20, le=200)
    ambient_c: float = Field(25, ge=-20, le=45)
    coolant_set_c: float = Field(20, ge=10, le=35)
    soh: float = Field(0.85, ge=0.65, le=1.0)
    channel_width_mm: float = Field(8, ge=4, le=10)
    channel_depth_mm: float = Field(3, ge=1, le=4)
    coolant_flow_lpm: float = Field(10, ge=1, le=30)


@app.post("/api/design-sweep")
async def design_sweep(req: SweepRequest) -> dict:
    current = (req.channel_width_mm, req.channel_depth_mm, req.coolant_flow_lpm)
    return await asyncio.to_thread(run_design_sweep, req.charge_power_kw, req.ambient_c, req.coolant_set_c, req.soh, current)


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    @app.get("/")
    async def root() -> dict:
        return {"message": "frontend/dist 가 없습니다. frontend 폴더에서 npm run build 를 실행하세요."}
