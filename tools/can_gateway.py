"""
CAN 게이트웨이 — 차량 CAN 버스의 BMS 프레임을 DBC 로 해석해 ThermalTwin(/ws/ingest)으로 보내고,
서버의 권고 명령을 TT_Command 프레임으로 다시 CAN 에 내보냅니다.

  CAN 버스 ─(python-can)→ DBC 해석(cantools) → 프레임 모으기·신선도 검사 → JSON 텔레메트리 → ws(s)://…/ws/ingest
         ←──────────────── TT_Command (권고: 충전 출력 상한·냉각수 유량·위험 등급) ←── 제어 명령 응답

실제 차량:   python tools/can_gateway.py --interface pcan --channel PCAN_USBBUS1 --bitrate 500000 \
                 --dbc vehicle.dbc --probe-map probe_map.json --vehicle EV001 --url wss://server:8443/ws/ingest --token …
로그 재생:   python tools/can_gateway.py --log drive.asc   (candump .log / Vector .asc / .blf / .csv)
시연(장비 없음): python tools/can_gateway.py --demo    → 같은 프로세스에 가상 BMS 를 띄워 가상 CAN 버스로 주고받음

DBC 가 다른 차량이면 --dbc 와 아래 SIGNALS 표(신호 이름 대응)만 바꾸면 됩니다.
프로브 위치는 CAN 에 없으므로 --probe-map JSON([[모듈, 셀], …], 프로브 번호 순서)으로 줍니다.
TT_Command 는 '권고'입니다. 차량 쪽 BMS 가 자체 기능안전 판단(ISO 26262) 없이 그대로 적용하면 안 됩니다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

try:
    import can
    import cantools
except ImportError:
    print("python-can 과 cantools 가 필요합니다:  backend\\.venv\\Scripts\\python.exe -m pip install -r tools\\requirements-can.txt")
    raise SystemExit(1) from None
import websockets  # noqa: E402

import estimator as est  # noqa: E402
import thermal_model as tm  # noqa: E402

from paths import TOOLS_DIR  # noqa: E402  (배포판에서도 DBC 위치를 맞게 찾음)

DEFAULT_DBC = TOOLS_DIR / "thermaltwin_bms.dbc"

# 이 게이트웨이가 쓰는 의미 → DBC 메시지·신호 이름 (다른 DBC 를 쓰면 여기만 고침)
SIGNALS = {
    "status": "BMS_PackStatus", "current": "PackCurrent", "soc": "PackSOC",
    "coolant_in": "CoolantInletTemp", "ambient": "AmbientTemp",
    "uptime_msg": "BMS_Uptime", "uptime": "UptimeSec",
    "cells": "BMS_CellVoltages", "cell_block": "CellBlock", "cell_prefix": "CellV", "cells_per_block": 4,
    "probes": "BMS_ProbeTemps", "probe_block": "ProbeBlock", "probe_prefix": "ProbeT", "probes_per_block": 6,
    "command": "TT_Command",
}
PROBE_SNA_C = 215.0          # 원시값 0x1FF (신호 없음) → 215.5 °C 로 해석되므로 이 이상은 버림
RISK_CODE = {"Safe": 0, "Warning": 1, "Danger": 2}
MODE_CODE = {"twin": 0, "fallback": 1, "off": 2}
REPLY_TIMEOUT_S = 2.0        # 서버 응답이 이보다 늦으면 TT_Command 를 보내지 않음 → BMS 워치독이 자체 규칙으로 전환


class FrameAssembler:
    """여러 CAN 프레임에 나뉘어 오는 값을 모아 텔레메트리 1건을 만든다. 오래된 값(max_age 초)은 쓰지 않는다."""

    def __init__(self, db: cantools.database.Database, probe_cells: list[list[int]], max_age: float = 2.0):
        self.db, self.probe_cells, self.max_age = db, probe_cells, max_age
        self.ids = {db.get_message_by_name(SIGNALS[k]).frame_id: k
                    for k in ("status", "uptime_msg", "cells", "probes") if self._has(SIGNALS[k])}
        self.status: dict | None = None
        self.status_t = 0.0
        self.uptime: float | None = None
        self.cells: dict[int, tuple[float, float]] = {}     # 셀 번호 → (전압, 받은 시각)
        self.probes: dict[int, tuple[float, float]] = {}
        self.decode_errors = 0
        self.lock = threading.Lock()

    def _has(self, name: str) -> bool:
        try:
            self.db.get_message_by_name(name)
            return True
        except KeyError:
            return False

    def feed(self, msg: can.Message) -> None:
        kind = self.ids.get(msg.arbitration_id)
        if kind is None or msg.is_error_frame:
            return
        try:
            d = self.db.decode_message(msg.arbitration_id, msg.data, decode_choices=False)
        except Exception:  # noqa: BLE001  (길이 불일치·손상 프레임)
            self.decode_errors += 1
            return
        now = time.monotonic()
        with self.lock:
            if kind == "status":
                self.status, self.status_t = d, now
            elif kind == "uptime_msg":
                self.uptime = float(d[SIGNALS["uptime"]])
            elif kind == "cells":
                base = int(d[SIGNALS["cell_block"]]) * SIGNALS["cells_per_block"]
                for n in range(SIGNALS["cells_per_block"]):
                    if base + n < tm.N_CELLS:
                        self.cells[base + n] = (float(d[f"{SIGNALS['cell_prefix']}{n}"]), now)
            elif kind == "probes":
                base = int(d[SIGNALS["probe_block"]]) * SIGNALS["probes_per_block"]
                for n in range(SIGNALS["probes_per_block"]):
                    v = float(d[f"{SIGNALS['probe_prefix']}{n}"])
                    if base + n < len(self.probe_cells) and v < PROBE_SNA_C:
                        self.probes[base + n] = (v, now)

    def telemetry(self, vehicle_id: str, t0: float) -> dict | None:
        now = time.monotonic()
        with self.lock:
            if self.status is None or now - self.status_t > self.max_age:
                return None
            st = self.status
            fresh_cells = {i: v for i, (v, t) in self.cells.items() if now - t <= self.max_age}
            fresh_probes = {i: v for i, (v, t) in self.probes.items() if now - t <= self.max_age}
            uptime = self.uptime
        msg = {
            "vehicle_id": vehicle_id,
            "t_s": round(uptime if uptime is not None else now - t0, 1),
            "current_a": float(st[SIGNALS["current"]]),
            "soc": float(st[SIGNALS["soc"]]) / 100.0,
            "t_coolant_in": float(st[SIGNALS["coolant_in"]]),
            "t_ambient": float(st[SIGNALS["ambient"]]),
            # 셀 전압은 96개가 모두 신선할 때만 보냄 (일부만 오래되면 셀별 저항 추정을 망가뜨림)
            "v_cells": [fresh_cells[i] for i in range(tm.N_CELLS)] if len(fresh_cells) == tm.N_CELLS else None,
            "probe_cells": [self.probe_cells[i] for i in sorted(fresh_probes)],
            "probe_t": [fresh_probes[i] for i in sorted(fresh_probes)],
        }
        return msg


def encode_command(db, reply: dict, counter: int) -> can.Message:
    m = db.get_message_by_name(SIGNALS["command"])
    data = m.encode({
        "ChargeLimitKw": max(0.0, min(6553.0, float(reply.get("charge_limit_kw") or 0.0))),
        "CoolantFlowLpm": max(0.0, min(102.0, float(reply.get("coolant_flow_lpm") or 0.0))),
        "CoolantSetTemp": max(-40.0, min(87.0, float(reply.get("coolant_set_c") or 20.0))),
        "DeratePct": round(100 * max(0.0, min(1.0, float(reply.get("derate", 1.0))))),
        "RiskLevel": RISK_CODE.get(reply.get("risk_status"), 0),
        "CmdCounter": counter % 16,
        **({"ControlMode": MODE_CODE.get(reply.get("mode"), 0)} if any(s.name == "ControlMode" for s in m.signals) else {}),
    })
    return can.Message(arbitration_id=m.frame_id, data=data, is_extended_id=False)


# ─────────────────────────────────────────────
# 시연용 가상 BMS (실제 차량 대신 가상 CAN 버스에 BMS 프레임을 내보냄)
# ─────────────────────────────────────────────
class DemoBMS(threading.Thread):
    def __init__(self, db, channel: str, probe_cells: list[list[int]], speed: float, power: float, soh: float):
        super().__init__(daemon=True)
        from sim_vehicle import SimVehicle  # noqa: PLC0415
        self.db, self.speed = db, speed
        self.bus = can.Bus(interface="virtual", channel=channel, receive_own_messages=False)
        self.car = SimVehicle(seed=7)
        self.car.ctrl.charge_power_kw, self.car.ctrl.true_soh, self.car.ctrl.coolant_flow_lpm = power, soh, 8.0
        self.car.reset()
        self.sensor_idx = [est.cell_index(r, c) for r, c in probe_cells]
        self.cmd_id = db.get_message_by_name(SIGNALS["command"]).frame_id
        self.stop = threading.Event()
        self.counter = 0
        self.applied: dict = {}

    def _send(self, name: str, values: dict) -> None:
        m = self.db.get_message_by_name(name)
        self.bus.send(can.Message(arbitration_id=m.frame_id, data=m.encode(values), is_extended_id=False))

    def run(self) -> None:
        while not self.stop.is_set():
            for _ in range(int(self.speed)):
                self.car.step(1.0)
            tel = self.car.telemetry(self.sensor_idx)
            self.counter += 1
            self._send(SIGNALS["status"], {"PackCurrent": tel["current_a"], "PackSOC": tel["soc"] * 100,
                                          "CoolantInletTemp": tel["t_coolant_in"], "AmbientTemp": tel["t_ambient"],
                                          "RollingCounter": self.counter % 16})
            self._send(SIGNALS["uptime_msg"], {"UptimeSec": tel["t_s"]})
            v = tel["v_cells"]
            for b in range(tm.N_CELLS // 4):
                self._send(SIGNALS["cells"], {"CellBlock": b, **{f"CellV{n}": v[b * 4 + n] for n in range(4)}})
            pt = list(tel["probe_t"])
            for b in range((len(pt) + 5) // 6):
                vals = {"ProbeBlock": b}
                for n in range(6):
                    k = b * 6 + n
                    vals[f"ProbeT{n}"] = pt[k] if k < len(pt) else 215.5   # 0x1FF = 신호 없음
                self._send(SIGNALS["probes"], vals)
            # 게이트웨이가 보낸 TT_Command 를 받아 적용 (실제 BMS 라면 자체 안전 판단 후)
            deadline = time.monotonic() + 1.0
            while (rest := deadline - time.monotonic()) > 0:
                msg = self.bus.recv(timeout=rest)
                if msg is not None and msg.arbitration_id == self.cmd_id:
                    d = self.db.decode_message(msg.arbitration_id, msg.data, decode_choices=False)
                    self.car.power_limit_kw = float(d["ChargeLimitKw"])
                    self.car.ctrl.coolant_flow_lpm = max(1.0, float(d["CoolantFlowLpm"]))
                    self.applied = d
        self.bus.shutdown()


# ─────────────────────────────────────────────
def load_probe_map(path: str | None, n_default: int) -> list[list[int]]:
    if path:
        cells = json.loads(Path(path).read_text(encoding="utf-8"))
        cells = cells.get("probe_cells", cells) if isinstance(cells, dict) else cells
        return [[int(r), int(c)] for r, c in cells]
    return [est.index_cell(i) for i in est.uniform_layout(n_default)]


def ssl_context(url: str, insecure: bool, cafile: str | None):
    if not url.startswith("wss://"):
        return None
    ctx = ssl.create_default_context(cafile=cafile)
    if insecure:           # 자체 서명 인증서 시험용
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def run(a) -> None:
    db = cantools.database.load_file(a.dbc)
    probe_cells = load_probe_map(a.probe_map, a.probes)
    asm = FrameAssembler(db, probe_cells, max_age=a.max_age)
    demo = None
    if a.demo:
        channel = "thermaltwin_demo"
        demo = DemoBMS(db, channel, probe_cells, a.speed, a.power, a.soh)
        bus = can.Bus(interface="virtual", channel=channel, receive_own_messages=False)
        demo.start()
    elif a.log:
        bus = None
    else:
        bus = can.Bus(interface=a.interface, channel=a.channel, bitrate=a.bitrate)

    notifier = None
    if bus is not None:
        notifier = can.Notifier(bus, [asm.feed])
    else:
        def replay():
            # 로그 파일을 기록된 시간 간격대로 다시 흘려 보냄
            for msg in can.MessageSync(can.LogReader(a.log), timestamps=True):
                asm.feed(msg)
        threading.Thread(target=replay, daemon=True).start()

    url = a.url
    token = a.token or os.environ.get("THERMALTWIN_INGEST_TOKEN")
    if token:
        url += ("&" if "?" in url else "?") + "token=" + quote(token)
    t0 = time.monotonic()
    counter = 0
    late = 0
    print(f"[CAN] DBC {Path(a.dbc).name} · 프로브 {len(probe_cells)}개 · 차량 {a.vehicle} → {a.url}", flush=True)
    try:
        async with websockets.connect(url, max_size=2**22, ssl=ssl_context(url, a.insecure, a.cafile)) as ws:
            print("[CAN] 서버 연결됨 (Ctrl+C 로 종료)", flush=True)
            while True:
                await asyncio.sleep(a.period)
                tel = asm.telemetry(a.vehicle, t0)
                if tel is None:
                    print("[CAN] BMS_PackStatus 대기 중…", flush=True)
                    continue
                while late:      # 늦게 도착한 이전 응답은 버림 (오래된 명령을 보내지 않음)
                    try:
                        await asyncio.wait_for(ws.recv(), 0.5)
                        late -= 1
                    except asyncio.TimeoutError:
                        break
                await ws.send(json.dumps(tel))
                try:
                    reply = json.loads(await asyncio.wait_for(ws.recv(), REPLY_TIMEOUT_S))
                except asyncio.TimeoutError:
                    late += 1
                    print(f"[CAN] 서버 응답 {REPLY_TIMEOUT_S:g} s 초과 — TT_Command 를 보내지 않음 "
                          "(CmdCounter 가 멈추면 BMS 는 자체 규칙으로 전환)", flush=True)
                    continue
                if reply.get("type") != "commands":
                    print("[서버 거부]", reply.get("message"), flush=True)
                    continue
                counter += 1
                if bus is not None and not a.no_command:
                    bus.send(encode_command(db, reply, counter))
                warn = f" ⚠ {'; '.join(reply['warnings'])}" if reply.get("warnings") else ""
                extra = ""
                if demo is not None:
                    tr = demo.car.truth()
                    extra = f" | 가상차량 실제 코어최고 {tr['core'].max():5.1f}°C, 적용 상한 {demo.applied.get('ChargeLimitKw', '--')} kW"
                print(f"t={tel['t_s'] / 60:6.1f}분 SOC {tel['soc'] * 100:5.1f}% I {tel['current_a']:7.1f} A "
                      f"셀전압 {'96' if tel['v_cells'] else '없음'} 프로브 {len(tel['probe_t'])} → "
                      f"{reply['risk_status']} · {'안전모드 ' if reply.get('mode') == 'fallback' else ''}"
                      f"상한 {reply['charge_limit_kw']} kW ({reply['reason']}){extra}{warn}", flush=True)
                if demo is not None and demo.car.mode == "rest" and demo.car.t > 600:
                    print("[CAN] 시연 충전 완료 — 종료", flush=True)
                    return
    finally:
        if notifier:
            notifier.stop()
        if demo:
            demo.stop.set()
            demo.join(timeout=3)
        if bus is not None:
            bus.shutdown()
        if asm.decode_errors:
            print(f"[CAN] 해석 실패 프레임 {asm.decode_errors}개", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="ThermalTwin CAN 게이트웨이")
    ap.add_argument("--url", default="ws://localhost:8000/ws/ingest")
    ap.add_argument("--token", help="서버의 THERMALTWIN_INGEST_TOKEN (환경변수로도 가능)")
    ap.add_argument("--insecure", action="store_true", help="wss 자체 서명 인증서 검증 생략 (시험용)")
    ap.add_argument("--cafile", help="wss 서버 인증서를 검증할 CA 파일")
    ap.add_argument("--vehicle", default="can_demo", help="vehicle_id")
    ap.add_argument("--dbc", default=str(DEFAULT_DBC))
    ap.add_argument("--probe-map", help="프로브 위치 JSON [[모듈,셀],…] (프로브 번호 순서)")
    ap.add_argument("--probes", type=int, default=16, help="--probe-map 이 없을 때 균등 배치 프로브 수")
    ap.add_argument("--interface", default="pcan", help="python-can 인터페이스 (pcan, kvaser, vector, socketcan, slcan …)")
    ap.add_argument("--channel", default="PCAN_USBBUS1")
    ap.add_argument("--bitrate", type=int, default=500000)
    ap.add_argument("--log", help="CAN 로그 파일 재생 (.asc .blf .log .csv)")
    ap.add_argument("--period", type=float, default=1.0, help="서버로 보내는 주기 [s]")
    ap.add_argument("--max-age", type=float, default=2.5, help="이보다 오래된 프레임 값은 버림 [s]")
    ap.add_argument("--no-command", action="store_true", help="TT_Command 를 CAN 으로 내보내지 않음 (수신 전용)")
    ap.add_argument("--demo", action="store_true", help="가상 CAN 버스 + 가상 BMS 로 시연")
    ap.add_argument("--speed", type=float, default=30.0, help="시연 배속")
    ap.add_argument("--power", type=float, default=190.0, help="시연 충전 출력 kW")
    ap.add_argument("--soh", type=float, default=0.78)
    a = ap.parse_args()
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print("[CAN] 서버에 연결할 수 없습니다. 2_start.bat 이 켜져 있는지 확인하세요.", e)
    except websockets.InvalidStatus as e:
        print("[CAN] 서버가 연결을 거부했습니다 (토큰 확인):", e)


if __name__ == "__main__":
    main()
