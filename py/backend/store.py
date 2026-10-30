"""
영구 로그 저장소 (SQLite, 표준 라이브러리만 사용) — 서버를 껐다 켜도 처리 기록이 남습니다.

  파일: logs/thermaltwin.db  (환경변수 THERMALTWIN_DB 로 경로 변경, "off" 면 저장 안 함)
  표:   log(차량·시각별 트윈 결과와 제어 명령), event(경고·입력 거부 등 사건 기록)
엔진 스레드와 이벤트 루프 양쪽에서 부르므로 내부 잠금으로 직렬화하고, 쓰기는 모아서 커밋합니다.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
import time
from pathlib import Path

from paths import LOG_DIR  # noqa: E402

DEFAULT_DB = LOG_DIR / "thermaltwin.db"

LOG_COLUMNS = ["vehicle_id", "wall_time", "t_s", "source", "current_a", "soc_pct", "core_max_hybrid_c",
               "core_max_pinn_nn_c", "surf_max_c", "sensor_max_c", "soh_est_pct", "risk_status",
               "runaway_hours_adiabatic", "cmd_charge_limit_kw", "cmd_coolant_flow_lpm"]
FLUSH_ROWS = 200
FLUSH_SECONDS = 2.0
MAX_ROWS = 2_000_000          # 이보다 많아지면 오래된 행부터 지움


class LogStore:
    def __init__(self, path: Path | str | None = None):
        env = os.environ.get("THERMALTWIN_DB")
        if env and env.lower() == "off":
            self.db = None
            return
        self.path = Path(env) if env else Path(path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        cols = ", ".join(f"{c} {'TEXT' if c in ('vehicle_id', 'source', 'risk_status') else 'REAL'}" for c in LOG_COLUMNS)
        self.db.execute(f"CREATE TABLE IF NOT EXISTS log ({cols})")
        self.db.execute("CREATE INDEX IF NOT EXISTS log_vt ON log(vehicle_id, wall_time)")
        self.db.execute("CREATE TABLE IF NOT EXISTS event (vehicle_id TEXT, wall_time REAL, kind TEXT, message TEXT)")
        self.db.commit()
        self.lock = threading.Lock()
        self.buf: list[tuple] = []
        self.last_flush = time.time()
        self.n_writes = 0

    @property
    def enabled(self) -> bool:
        return self.db is not None

    def add(self, row: dict) -> None:
        if self.db is None:
            return
        with self.lock:
            self.buf.append(tuple(row.get(c) for c in LOG_COLUMNS))
            if len(self.buf) >= FLUSH_ROWS or time.time() - self.last_flush > FLUSH_SECONDS:
                self._flush()

    def event(self, vehicle_id: str, kind: str, message: str) -> None:
        if self.db is None:
            return
        with self.lock:
            self.db.execute("INSERT INTO event VALUES (?,?,?,?)", (vehicle_id, time.time(), kind, message[:500]))
            self.db.commit()

    def flush(self) -> None:
        if self.db is None:
            return
        with self.lock:
            self._flush()

    def _flush(self) -> None:
        if self.buf:
            self.db.executemany(f"INSERT INTO log VALUES ({','.join('?' * len(LOG_COLUMNS))})", self.buf)
            self.n_writes += 1
            if self.n_writes % 500 == 0:
                self.db.execute("DELETE FROM log WHERE rowid <= (SELECT MAX(rowid) FROM log) - ?", (MAX_ROWS,))
            self.db.commit()
            self.buf.clear()
        self.last_flush = time.time()

    def vehicles(self) -> list[dict]:
        if self.db is None:
            return []
        self.flush()
        with self.lock:
            rows = self.db.execute("SELECT vehicle_id, COUNT(*), MIN(wall_time), MAX(wall_time) FROM log GROUP BY vehicle_id").fetchall()
        return [{"vehicle_id": v, "rows": n, "first": t0, "last": t1} for v, n, t0, t1 in rows]

    def csv(self, vehicle_id: str | None = None, since: float | None = None, limit: int = 500_000) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(LOG_COLUMNS)
        if self.db is None:
            return buf.getvalue()
        self.flush()
        q, args = "SELECT * FROM log WHERE 1=1", []
        if vehicle_id:
            q += " AND vehicle_id = ?"
            args.append(vehicle_id)
        if since is not None:
            q += " AND wall_time >= ?"
            args.append(since)
        q += " ORDER BY rowid DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        w.writerows(reversed(rows))
        return buf.getvalue()

    def events(self, vehicle_id: str | None = None, limit: int = 200) -> list[dict]:
        if self.db is None:
            return []
        q, args = "SELECT * FROM event", []
        if vehicle_id:
            q += " WHERE vehicle_id = ?"
            args.append(vehicle_id)
        q += " ORDER BY rowid DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.db.execute(q, args).fetchall()
        return [{"vehicle_id": v, "wall_time": t, "kind": k, "message": m} for v, t, k, m in rows]

    def close(self) -> None:
        if self.db is not None:
            self.flush()
            self.db.close()
            self.db = None
