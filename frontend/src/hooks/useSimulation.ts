import { useCallback, useEffect, useRef, useState } from 'react';
import type { ConnectionStatus, Frame } from '../types';

const MAX_HISTORY = 900;
const RECONNECT_DELAY_MS = 2000;

function wsUrl(): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${window.location.host}/ws/battery-simulation`;
}

export interface HistoryPoint {
  t_min: number;
  core: number;
  core_nn: number;
  sensor_max: number | null;
  true_core?: number;
  soc: number;
  power: number;
  heat: number;
  limit: number;
  soh_est: number | null;
  soh_band: [number, number] | null;   // 95 % 신뢰구간 (차트 띠)
}

function toHistory(f: Frame): HistoryPoint {
  return {
    t_min: Math.round((f.t_sim_s / 60) * 100) / 100,
    core: f.kpi.core_hybrid_max_c,
    core_nn: f.kpi.core_nn_max_c,
    sensor_max: f.kpi.sensor_max_c,
    true_core: f.twin.true_core_max_c,
    soc: f.current_soc,
    power: f.power_kw,
    heat: f.heat_kw,
    limit: f.commands.charge_limit_kw,
    soh_est: f.twin.soh_est,
    soh_band: f.twin.soh_lo !== null && f.twin.soh_hi !== null ? [f.twin.soh_lo, f.twin.soh_hi] : null,
  };
}

/** 서버 엔진과 웹소켓 연결 (useEffect) — 여러 화면이 같은 엔진을 공유 */
export function useSimulation() {
  const [latest, setLatest] = useState<Frame | null>(null);
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const [retryKey, setRetryKey] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);

  const send = useCallback((payload: object) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload));
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    let cancelled = false;
    const ws = new WebSocket(wsUrl());
    wsRef.current = ws;
    setStatus('connecting');
    ws.onopen = () => {
      setStatus('open');
      setHistory([]);
    };
    ws.onmessage = (ev: MessageEvent<string>) => {
      try {
        const frame = JSON.parse(ev.data) as Frame;
        setLatest(frame);
        setHistory((prev) => {
          const last = prev[prev.length - 1];
          const p = toHistory(frame);
          if (last && p.t_min === last.t_min) return prev;
          const base = last && p.t_min < last.t_min ? [] : prev;
          const next = [...base, p];
          return next.length > MAX_HISTORY ? next.slice(next.length - MAX_HISTORY) : next;
        });
      } catch {
        /* 무시 */
      }
    };
    ws.onclose = () => {
      if (cancelled) return;
      setStatus('closed');
      timer = window.setTimeout(() => setRetryKey((k) => k + 1), RECONNECT_DELAY_MS);
    };
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      ws.onmessage = null;
      ws.onclose = null;
      if (ws.readyState === WebSocket.OPEN) ws.close();
      else if (ws.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close();
      wsRef.current = null;
    };
  }, [retryKey]);

  const clearHistory = useCallback(() => setHistory([]), []);
  return { latest, history, status, send, clearHistory };
}
