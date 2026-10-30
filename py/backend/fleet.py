"""
여러 차량 동시 처리 — vehicle_id 마다 독립된 디지털 트윈(Engine)을 둡니다.

  "default"  화면의 기본 엔진 (시뮬레이션·실차 재생·외부 입력을 오가는 v0.3 동작 그대로)
  그 밖의 id 외부 게이트웨이가 vehicle_id 를 붙여 보내면 처음 받을 때 엔진을 새로 만듦 (학습 모델은 공유)
화면은 한 번에 한 차량을 보고(view), 차량 목록 요약은 매 프레임에 같이 보냅니다.
"""
from __future__ import annotations

import time

from engine import Engine
from store import LogStore

DEFAULT_ID = "default"
EXTERNAL_ID = "external"  # 사용자가 기본 차량을 다른 소스로 고정했을 때, 기본 차량 앞으로 온 외부 데이터를 받는 차량
MAX_VEHICLES = 50
STALE_SECONDS = 600       # 이 시간 동안 데이터가 없는 외부 차량은 목록에서 정리


class Fleet:
    def __init__(self, store: LogStore | None = None):
        self.store = store
        self.main = Engine(DEFAULT_ID, store=store)
        self.others: dict[str, Engine] = {}
        self.view_id = DEFAULT_ID
        self.gateways = 0

    def get(self, vehicle_id: str) -> Engine | None:
        return self.main if vehicle_id == DEFAULT_ID else self.others.get(vehicle_id)

    def for_ingest(self, vehicle_id: str) -> Engine:
        e = self.get(vehicle_id)
        if e is None:
            self._prune()
            if len(self.others) >= MAX_VEHICLES:
                raise ValueError(f"동시에 받을 수 있는 차량은 {MAX_VEHICLES}대까지입니다")
            e = self.others[vehicle_id] = Engine(vehicle_id, source="ingest", shared=self.main)
        return e

    def view(self) -> Engine:
        return self.get(self.view_id) or self.main

    def set_view(self, vehicle_id: str) -> None:
        if self.get(vehicle_id) is not None:
            self.view_id = vehicle_id

    def _prune(self) -> None:
        now = time.time()
        for vid, e in list(self.others.items()):
            last = e.ingest_state["last_rx"]
            if vid != self.view_id and last is not None and now - last > STALE_SECONDS:
                del self.others[vid]

    def summary(self) -> list[dict]:
        out = []
        now = time.time()
        for vid, e in [(DEFAULT_ID, self.main), *self.others.items()]:
            k = e.last
            core = float(max(e.core_estimate(k).max(), k["core_pinn"].max())) if k is not None else None
            last = e.ingest_state["last_rx"]
            out.append({"vehicle_id": vid, "source": e.ctrl.source, "risk_status": e.risk[0] if k is not None else None,
                        "core_max_c": round(core, 2) if core is not None else None,
                        "samples": e.ingest_state["samples"], "rejected": e.ingest_state["rejected"],
                        "age_s": round(now - last, 1) if last else None,
                        "charge_limit_kw": e.commands.get("charge_limit_kw"),
                        "control_mode": e.commands.get("mode"), "command_valid": e.command_status()["valid"]})
        return out
