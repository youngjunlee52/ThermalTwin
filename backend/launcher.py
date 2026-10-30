"""
ThermalTwin 실행 파일(ThermalTwin.exe) 진입점 — 더블클릭하면 서버를 켜고 브라우저를 엽니다.

  ThermalTwin.exe               서버 + 브라우저 (창을 닫으면 종료)
  ThermalTwin.exe can-demo      서버 + 가상 CAN 차량 시연 (화면 위 '차량'에서 can_demo 선택)
  ThermalTwin.exe gateway …     CAN 게이트웨이만 실행 (tools/can_gateway.py 와 같은 옵션)
  ThermalTwin.exe --no-browser  브라우저 자동 열기 안 함

소스에서도 같은 방식으로 실행됩니다:  backend\\.venv\\Scripts\\python.exe backend\\launcher.py
"""
from __future__ import annotations

import json
import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from paths import FROZEN, LOG_DIR, TOOLS_DIR

DEFAULT_PORT = 8000


def is_thermaltwin(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return json.loads(r.read()).get("service") == "ThermalTwin"
    except (OSError, ValueError):
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port() -> int:
    for p in [DEFAULT_PORT, *range(8001, 8051)]:
        if port_free(p):
            return p
    raise SystemExit("8000~8050 포트가 모두 사용 중입니다. 다른 프로그램을 닫고 다시 실행하세요.")


def wait_ready(port: int, timeout: float = 60.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if is_thermaltwin(port):
            return True
        time.sleep(0.3)
    return False


def start_server(port: int, in_thread: bool):
    print("모델을 불러오는 중… (처음에는 10~20초 걸릴 수 있습니다)", flush=True)
    import uvicorn

    import main  # noqa: PLC0415  (무거운 import: numpy·PINN·공분산)

    host = "0.0.0.0" if os.environ.get("THERMALTWIN_INGEST_TOKEN") else "127.0.0.1"
    config = uvicorn.Config(main.app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    if in_thread:
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        return server
    server.run()
    return server


def banner(port: int) -> None:
    url = f"http://localhost:{port}"
    print("=" * 60)
    print(f"  ThermalTwin v0.4 실행 중:  {url}")
    print("  브라우저가 열리지 않으면 위 주소를 직접 여세요.")
    print("  종료하려면 이 창을 닫으세요.")
    print(f"  기록 저장 위치: {LOG_DIR}")
    if not os.environ.get("THERMALTWIN_INGEST_TOKEN"):
        print("  외부 입력 토큰 없음 → 이 PC 에서만 접속됩니다.")
    print("=" * 60, flush=True)


def open_when_ready(port: int) -> None:
    def run():
        if wait_ready(port):
            webbrowser.open(f"http://localhost:{port}/")
    threading.Thread(target=run, daemon=True).start()


def run_gateway(argv: list[str]) -> None:
    sys.path.insert(0, str(TOOLS_DIR))
    import can_gateway  # noqa: PLC0415
    sys.argv = ["can_gateway", *argv]
    can_gateway.main()


def main() -> None:
    args = sys.argv[1:]
    browser = "--no-browser" not in args
    args = [a for a in args if a != "--no-browser"]

    if args[:1] == ["gateway"]:
        run_gateway(args[1:])
        return

    if is_thermaltwin(DEFAULT_PORT):
        port = DEFAULT_PORT
        print(f"ThermalTwin 이 이미 실행 중입니다 → http://localhost:{port}", flush=True)
        if args[:1] == ["can-demo"]:
            run_gateway(["--demo", "--vehicle", "can_demo", "--url", f"ws://127.0.0.1:{port}/ws/ingest"])
        elif browser:
            webbrowser.open(f"http://localhost:{port}/")
            time.sleep(2)
        return

    port = pick_port()
    if args[:1] == ["can-demo"]:
        start_server(port, in_thread=True)
        if not wait_ready(port):
            raise SystemExit("서버를 시작하지 못했습니다.")
        banner(port)
        if browser:
            webbrowser.open(f"http://localhost:{port}/")
        print("가상 CAN 차량 시연을 시작합니다. 화면 위 '차량' 목록에서 can_demo 를 고르세요.", flush=True)
        run_gateway(["--demo", "--vehicle", "can_demo", "--url", f"ws://127.0.0.1:{port}/ws/ingest"])
        print("시연이 끝났습니다. 서버는 계속 실행 중입니다 (창을 닫으면 종료).", flush=True)
        while True:
            time.sleep(3600)

    banner(port)
    if browser:
        open_when_ready(port)
    start_server(port, in_thread=False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except KeyboardInterrupt:
        pass
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code, flush=True)
            if FROZEN:
                input("Enter 를 누르면 창이 닫힙니다…")
        raise
    except Exception as e:  # noqa: BLE001  (배포판: 오류를 보여주고 창이 바로 닫히지 않게)
        import traceback
        traceback.print_exc()
        print(f"\n오류로 종료되었습니다: {e}", flush=True)
        if FROZEN:
            input("Enter 를 누르면 창이 닫힙니다…")
        sys.exit(1)
