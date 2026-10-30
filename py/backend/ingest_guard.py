"""
외부 입력(/ws/ingest · POST /api/telemetry) 보호 — 인증 + 물리적 타당성 검사

[인증]
  환경변수 THERMALTWIN_INGEST_TOKEN 을 지정하면 게이트웨이는 토큰을 보내야 합니다.
    HTTP:      Authorization: Bearer <토큰>
    WebSocket: ws://…/ws/ingest?token=<토큰>  (또는 같은 Authorization 헤더)
  지정하지 않으면 인증 없이 받습니다 (로컬 시연용). 서버 시작 로그에 경고가 찍힙니다.

[암호화]
  TLS 는 uvicorn 이 처리합니다:  --ssl-keyfile key.pem --ssl-certfile cert.pem  → wss:// · https://
  (2_start.bat 은 backend\\certs\\key.pem·cert.pem 이 있으면 자동으로 TLS 로 켭니다)

[타당성 검사]
  잘못된 값(센서 고장·단위 실수·CAN 해석 오류)이 트윈 상태를 망가뜨리지 않도록, 처리 전에 거릅니다.
  치명적 오류 → ValueError (샘플 거부), 경미한 이상 → 경고 목록 (샘플은 처리)
"""
from __future__ import annotations

import hmac
import math
import os
import re

import thermal_model as tm

INGEST_TOKEN = os.environ.get("THERMALTWIN_INGEST_TOKEN") or None
VEHICLE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

CURRENT_ABS_MAX_A = 2000.0
CELL_V_RANGE = (2.0, 4.5)          # NCM 셀 전압이 물리적으로 가능한 범위 [V]
PROBE_T_RANGE = (-40.0, 120.0)     # 온도 프로브 측정 범위 [°C]
COOLANT_T_RANGE = (-40.0, 90.0)
AMBIENT_T_RANGE = (-50.0, 60.0)
CELL_V_SPREAD_WARN = 0.3           # 셀 전압 편차가 이보다 크면 경고 (셀 이상 또는 배선 오류)
PROBE_JUMP_WARN_K_PER_S = 1.0      # 프로브 값이 초당 이보다 빨리 변하면 경고 (접촉 불량 의심)
SESSION_RESTART_BACKSTEP_S = 60.0  # 시각이 이보다 크게 되돌아가면 재시작으로 보고 트윈을 새로 시작 (작게 되돌아가면 거부)


def auth_required() -> bool:
    return INGEST_TOKEN is not None


def check_token(authorization: str | None, query_token: str | None) -> bool:
    if INGEST_TOKEN is None:
        return True
    given = query_token
    if authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    return given is not None and hmac.compare_digest(given.encode(), INGEST_TOKEN.encode())


def _finite(name: str, v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name}: 숫자가 아닙니다 ({v!r})") from e
    if not math.isfinite(x):
        raise ValueError(f"{name}: 유한한 값이 아닙니다 ({v!r})")
    return x


def _in_range(name: str, v, lo: float, hi: float) -> float:
    x = _finite(name, v)
    if not lo <= x <= hi:
        raise ValueError(f"{name}={x:g} 가 허용 범위 [{lo:g}, {hi:g}] 를 벗어났습니다")
    return x


def validate(s: dict, prev: dict | None) -> list[str]:
    """텔레메트리 1건 검사. 거부해야 하면 ValueError, 아니면 경고 문자열 목록. s 는 정규화된 값으로 고쳐 씀."""
    warnings: list[str] = []
    vid = s.get("vehicle_id") or "default"
    if not VEHICLE_ID_RE.match(vid):
        raise ValueError("vehicle_id 는 영문·숫자·_- 1~32자여야 합니다")
    s["vehicle_id"] = vid

    s["current_a"] = _in_range("current_a", s["current_a"], -CURRENT_ABS_MAX_A, CURRENT_ABS_MAX_A)
    soc = _finite("soc", s["soc"])
    soc = soc / 100.0 if soc > 1.0 else soc
    if not 0.0 <= soc <= 1.0:
        raise ValueError(f"soc={s['soc']} 는 0~1 또는 0~100 % 여야 합니다")
    s["soc"] = soc

    if s.get("t_s") is not None:
        s["t_s"] = _finite("t_s", s["t_s"])
        if prev is not None and prev.get("t_s") is not None and s["t_s"] <= prev["t_s"]:
            raise ValueError(f"t_s={s['t_s']:g} 가 이전 샘플({prev['t_s']:g})보다 늦지 않습니다 (중복·재전송 의심)")

    v = s.get("v_cells")
    if v is not None:
        if len(v) != tm.N_CELLS:
            raise ValueError(f"v_cells 는 {tm.N_CELLS}개여야 합니다 (받은 개수 {len(v)})")
        s["v_cells"] = [_in_range(f"v_cells[{i}]", x, *CELL_V_RANGE) for i, x in enumerate(v)]
        spread = max(s["v_cells"]) - min(s["v_cells"])
        if spread > CELL_V_SPREAD_WARN:
            warnings.append(f"셀 전압 편차 {spread * 1000:.0f} mV — 셀 이상 또는 배선·CAN 해석 오류 의심")

    cells = s.get("probe_cells") or []
    temps = s.get("probe_t") or []
    if len(cells) != len(temps):
        raise ValueError("probe_cells 와 probe_t 의 개수가 다릅니다")
    seen = set()
    for k, rc in enumerate(cells):
        if len(rc) != 2 or not (0 <= int(rc[0]) < tm.N_ROWS and 0 <= int(rc[1]) < tm.N_COLS):
            raise ValueError(f"probe_cells[{k}]={rc} 는 [모듈 0~{tm.N_ROWS - 1}, 셀 0~{tm.N_COLS - 1}] 이어야 합니다")
        key = (int(rc[0]), int(rc[1]))
        if key in seen:
            raise ValueError(f"probe_cells 에 같은 위치 {list(key)} 가 두 번 있습니다")
        seen.add(key)
    s["probe_t"] = [_in_range(f"probe_t[{k}]", x, *PROBE_T_RANGE) for k, x in enumerate(temps)]

    for key, rng in (("t_coolant_in", COOLANT_T_RANGE), ("t_ambient", AMBIENT_T_RANGE), ("coolant_flow_lpm", (0.0, 60.0))):
        if s.get(key) is not None:
            s[key] = _in_range(key, s[key], *rng)

    # 프로브 급변 (같은 위치 기준)
    if prev is not None and prev.get("t_s") is not None and s.get("t_s") is not None:
        dt = s["t_s"] - prev["t_s"]
        before = dict(zip(map(tuple, prev.get("probe_cells") or []), prev.get("probe_t") or []))
        for rc, t in zip(cells, s["probe_t"]):
            t0 = before.get((int(rc[0]), int(rc[1])))
            if t0 is not None and dt > 0 and abs(t - t0) / dt > PROBE_JUMP_WARN_K_PER_S:
                warnings.append(f"프로브 {list(rc)} 가 {abs(t - t0):.1f} K / {dt:g} s 급변 — 접촉 불량 의심")
    return warnings
