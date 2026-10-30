"""
외부 BMS/CAN 게이트웨이 시연 프로그램 — '다른 컴퓨터에 있는 실제 차량'처럼 동작합니다.

  1) 자기 쪽에서 차량(배터리팩)을 돌리면서 BMS 측정값(전류·SOC·셀 전압 96개·온도센서)을
  2) 네트워크로 ThermalTwin 서버(/ws/ingest)에 실시간 전송하고
  3) 서버가 돌려준 제어 명령(충전 출력 상한·냉각수 유량)을 차량에 실제로 적용합니다.
실제 차량에 붙일 때는 이 파일의 '차량' 부분을 CAN 수신(예: python-can + DBC 해석)으로 바꾸면 됩니다.

실행:  backend\\.venv\\Scripts\\python.exe tools\\fake_bms_gateway.py  (서버 2_start.bat 을 먼저 켜 두세요)
옵션:  --url ws://192.168.0.10:8000/ws/ingest   --sensors 8   --speed 20   --power 180   --vehicle EV002   --token …
CAN 버스에서 직접 받으려면 tools/can_gateway.py 를 쓰세요.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import websockets  # noqa: E402

import estimator as est  # noqa: E402
from sim_vehicle import SimVehicle  # noqa: E402


async def run(url: str, n_sensors: int, speed: float, power: float, soh: float, vehicle: str, token: str | None) -> None:
    if token:
        url += ("&" if "?" in url else "?") + "token=" + quote(token)
    car = SimVehicle(seed=42)
    car.ctrl.charge_power_kw = power
    car.ctrl.true_soh = soh
    car.ctrl.coolant_flow_lpm = 8.0
    car.reset()
    sensors = est.uniform_layout(n_sensors)
    print(f"[게이트웨이] {url} 에 연결 중… (센서 {len(sensors)}개, 배속 ×{speed:g})")
    async with websockets.connect(url, max_size=2**22) as ws:
        print("[게이트웨이] 연결됨 — 텔레메트리 전송 시작 (Ctrl+C 로 종료)")
        while True:
            for _ in range(int(speed)):
                car.step(1.0)
            tel = car.telemetry(sensors)
            msg = {k: tel[k] for k in ("t_s", "current_a", "soc", "v_cells", "probe_t", "t_coolant_in", "t_ambient",
                                       "coolant_flow_lpm")}
            msg["probe_cells"] = [est.index_cell(i) for i in sensors]
            msg["vehicle_id"] = vehicle
            await ws.send(json.dumps(msg))
            reply = json.loads(await ws.recv())
            if reply.get("type") == "commands":
                car.power_limit_kw = reply.get("charge_limit_kw")
                car.ctrl.coolant_flow_lpm = float(reply.get("coolant_flow_lpm", car.ctrl.coolant_flow_lpm))
                tr = car.truth()
                print(f"t={car.t / 60:5.1f}분 SOC {car.plant.soc[0] * 100:5.1f}%  전류 {car.current:7.1f} A  "
                      f"실제 코어최고 {tr['core'].max():5.1f}°C  ← 명령: 충전상한 {reply['charge_limit_kw']:6.1f} kW, "
                      f"유량 {reply['coolant_flow_lpm']:4.1f} L/min ({reply['reason']})", flush=True)
            else:
                print("[서버 오류]", reply)
            if car.mode == "rest" and car.t > 600:
                print("[게이트웨이] 충전 완료 — 종료")
                return
            await asyncio.sleep(1.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://localhost:8000/ws/ingest")
    ap.add_argument("--sensors", type=int, default=8)
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--power", type=float, default=180.0)
    ap.add_argument("--soh", type=float, default=0.78)
    ap.add_argument("--vehicle", default="default", help="vehicle_id (default = 화면 기본 차량)")
    ap.add_argument("--token", default=os.environ.get("THERMALTWIN_INGEST_TOKEN"), help="서버 인증 토큰")
    a = ap.parse_args()
    try:
        asyncio.run(run(a.url, a.sensors, a.speed, a.power, a.soh, a.vehicle, a.token))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print("[게이트웨이] 서버에 연결할 수 없습니다. 2_start.bat 이 켜져 있는지 확인하세요.", e)


if __name__ == "__main__":
    main()
