import { useEffect, useRef, useState } from 'react';
import type { Controls, Dataset, Frame } from '../../types';
import { SOURCE_LABEL } from '../../types';
import { Card } from '../ui';

const EXAMPLE = `{
  "vehicle_id": "EV001",         // 차량 식별자 (선택, 여러 대 동시 수신)
  "t_s": 1200.0,                 // 시각 [s] (이전 샘플보다 커야 함)
  "current_a": -320.5,           // 팩 전류 [A] (+방전, −충전)
  "soc": 0.47,                   // 0~1 또는 %
  "v_cells": [3.912, 3.915, …],  // 셀 전압 96개 [V] (선택, 있으면 정확도↑)
  "probe_cells": [[0,1],[0,7],…],// 온도센서 위치 [모듈 0~7, 셀 0~11]
  "probe_t": [31.2, 32.0, …],    // 온도센서 값 [°C]  (개수·위치 자유)
  "t_coolant_in": 20.5,          // 냉각수 입구온도 (선택)
  "t_ambient": 25.0              // 외기온도 (선택)
}`;

interface StoredEvent { vehicle_id: string; wall_time: number; kind: string; message: string }

const EVENT_KIND: Record<string, { label: string; color: string }> = {
  rejected: { label: '거부', color: 'var(--color-critical)' },
  warning: { label: '경고', color: 'var(--color-warning)' },
  fallback_on: { label: '안전 모드 전환', color: 'var(--color-serious)' },
  fallback_off: { label: 'AI 제어 복귀', color: 'var(--color-good)' },
  restart: { label: '재시작', color: 'var(--color-ink-2)' },
  sensor_fault: { label: '센서 이상', color: 'var(--color-serious)' },
  sensor_ok: { label: '센서 복귀', color: 'var(--color-good)' },
};

export default function DataView({ latest: f, controls, onChange, onViewVehicle }: {
  latest: Frame | null; controls: Controls; onChange: (p: Partial<Controls>) => void; onViewVehicle: (id: string) => void;
}) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [events, setEvents] = useState<StoredEvent[]>([]);
  const [msg, setMsg] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const host = typeof window !== 'undefined' ? window.location.host : 'localhost:8000';
  const load = () => fetch('/api/datasets').then((r) => r.json()).then(setDatasets).catch(() => setDatasets([]));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const get = () => fetch('/api/events?limit=20').then((r) => r.json()).then(setEvents).catch(() => setEvents([]));
    get();
    const id = window.setInterval(get, 5000);
    return () => window.clearInterval(id);
  }, []);
  const vehicles = f?.fleet?.vehicles ?? [];
  const auth = f?.security?.auth_required;

  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setMsg('업로드·변환 중…');
    const name = file.name.replace(/\.[^.]+$/, '');
    try {
      const r = await fetch(`/api/datasets/upload?name=${encodeURIComponent(name)}`, { method: 'POST', body: file });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail ?? r.status);
      setMsg(`완료: ${d.dataset} (${d.rows}행) — 아래 목록에서 재생하세요`);
      load();
    } catch (e) {
      setMsg(`실패: ${String(e)}`);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="현재 데이터 흐름">
          <div className="space-y-2 text-sm text-ink-2">
            <p>소스: <b className="text-ink">{f ? SOURCE_LABEL[f.source] : '--'}</b> · 처리한 샘플(로그) {f?.log_rows.toLocaleString() ?? 0}개</p>
            <p>외부 게이트웨이 연결: <b className="text-ink">{f?.ingest.connected ?? 0}</b>개 · 수신 {f?.ingest.samples ?? 0}건{f?.ingest.age_s !== null && f?.ingest.age_s !== undefined ? ` · 마지막 ${f.ingest.age_s}초 전` : ''}</p>
            <div className="rounded-lg bg-panel-2 p-3 font-mono text-xs leading-relaxed">
              <p className="mb-1 font-sans text-[11px] text-muted">최신 제어 명령 (GET /api/commands)</p>
              충전 출력 상한 {f?.commands.charge_limit_kw ?? '--'} kW · 냉각수 유량 {f?.commands.coolant_flow_lpm ?? '--'} L/min · 목표 {f?.commands.coolant_set_c ?? '--'} °C<br />
              사유: {f?.commands.reason ?? '--'}
            </div>
            <div className="flex flex-wrap gap-2">
              <a href="/api/log.csv" className="inline-block rounded-md bg-accent px-3 py-2 text-xs font-semibold text-white">⬇ 이번 실행 로그 (보는 차량)</a>
              {f?.security?.db_enabled && (
                <a href={`/api/history.csv?vehicle=${encodeURIComponent(f.vehicle_id)}`}
                  className="inline-block rounded-md bg-panel-2 px-3 py-2 text-xs font-semibold text-ink ring-1 ring-line hover:bg-line">⬇ 영구 기록 (SQLite)</a>
              )}
            </div>
            <p className="text-xs text-muted">
              영구 기록: {f?.security?.db_enabled ? <>logs/thermaltwin.db 에 저장 중 (서버를 다시 켜도 남음)</> : '꺼짐 (THERMALTWIN_DB=off)'}
            </p>
          </div>
        </Card>

        <Card title="외부 실시간 데이터 연결 (실차 · BMS · CAN 게이트웨이)">
          <ul className="space-y-1 text-sm text-ink-2">
            <li>웹소켓: <code className="font-mono text-ink">ws://{host}/ws/ingest{auth ? '?token=…' : ''}</code> — 1건 보낼 때마다 제어 명령이 바로 돌아옵니다</li>
            <li>HTTP: <code className="font-mono text-ink">POST http://{host}/api/telemetry</code>{auth && <> (헤더 <code className="font-mono">Authorization: Bearer …</code>)</>}</li>
            <li>
              인증: {auth
                ? <b style={{ color: 'var(--color-good)' }}>토큰 필요 (THERMALTWIN_INGEST_TOKEN)</b>
                : <b style={{ color: 'var(--color-warning)' }}>꺼짐 — 다른 기기에서 보내게 하려면 THERMALTWIN_INGEST_TOKEN 을 설정하세요</b>}
            </li>
            <li>받는 값은 처리 전에 검사합니다 (셀 전압 96개·범위, 프로브 위치·온도 범위, 시각 역행). 이상하면 거부하고 아래 기록에 남깁니다</li>
          </ul>
          <pre className="mt-2 overflow-x-auto rounded-lg bg-panel-2 p-3 font-mono text-[11px] leading-relaxed text-ink-2">{EXAMPLE}</pre>
          <p className="mt-2 text-xs text-muted">
            시연: <code className="font-mono">4_demo_gateway.bat</code> (JSON) · <code className="font-mono">6_demo_can_gateway.bat</code> (가상 CAN 버스 + DBC 해석, 명령을 TT_Command 프레임으로 BMS 에 돌려줌).
            실제 차량: <code className="font-mono">tools/can_gateway.py --interface pcan --dbc 차량.dbc --probe-map 위치.json</code>
          </p>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title={`수신 차량 (${vehicles.length}대)`}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs">
              <thead className="text-muted"><tr className="border-b border-line">
                {['차량', '소스', '상태', '코어 최고', '수신 / 거부', '마지막 수신', ''].map((h) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}
              </tr></thead>
              <tbody className="text-ink-2">
                {vehicles.map((v) => (
                  <tr key={v.vehicle_id} className="border-b border-line/60">
                    <td className="px-2 py-1.5 font-mono text-ink">{v.vehicle_id === 'default' ? '기본' : v.vehicle_id}</td>
                    <td className="px-2 py-1.5">{SOURCE_LABEL[v.source]}</td>
                    <td className="px-2 py-1.5">{v.risk_status ?? '--'}</td>
                    <td className="px-2 py-1.5 font-mono">{v.core_max_c?.toFixed(1) ?? '--'} °C</td>
                    <td className="px-2 py-1.5 font-mono">{v.samples} / {v.rejected}</td>
                    <td className="px-2 py-1.5 font-mono">{v.age_s !== null ? `${v.age_s}s 전` : '--'}</td>
                    <td className="px-2 py-1.5 text-right">
                      <button type="button" onClick={() => onViewVehicle(v.vehicle_id)}
                        className={`rounded px-2 py-1 ${f?.vehicle_id === v.vehicle_id ? 'bg-accent text-white' : 'bg-panel-2 text-ink ring-1 ring-line hover:bg-line'}`}>
                        보기
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="입력 거부 · 이상 · 안전 모드 기록 (최근 20건)">
          {events.length === 0 ? <p className="text-sm text-muted">기록 없음</p> : (
            <ul className="max-h-60 space-y-1 overflow-y-auto font-mono text-[11px] text-ink-2">
              {events.map((e, i) => (
                <li key={i}>
                  <span className="text-muted">{new Date(e.wall_time * 1000).toLocaleTimeString()}</span>{' '}
                  <span style={{ color: (EVENT_KIND[e.kind] ?? EVENT_KIND.warning).color }}>{(EVENT_KIND[e.kind] ?? EVENT_KIND.warning).label}</span>{' '}
                  [{e.vehicle_id}] {e.message}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="실차 데이터">
        <p className="mb-3 text-sm text-ink-2">
          포함된 데이터: <b className="text-ink">300대 EV 필드 데이터</b>의 한 차량 (96S NCM 155 Ah, 셀 전압 96 + 온도 프로브 32, 10초 샘플).
          Liu et al., Nature Communications 16, 1137 (2025), MIT License.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-xs">
            <thead className="text-muted"><tr className="border-b border-line">
              {['구간', '길이', 'SOC', '평균 전류', '프로브 최고 (시작→최고)', ''].map((h) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}
            </tr></thead>
            <tbody className="text-ink-2">
              {datasets.map((d) => (
                <tr key={d.name} className="border-b border-line/60">
                  <td className="px-2 py-1.5 text-ink">{d.title}</td>
                  <td className="px-2 py-1.5 font-mono">{d.duration_min}분 · {d.rows}행</td>
                  <td className="px-2 py-1.5 font-mono">{d.soc_start} → {d.soc_end} %</td>
                  <td className="px-2 py-1.5 font-mono">{d.current_mean_a} A</td>
                  <td className="px-2 py-1.5 font-mono">{d.probe_max_start_c} → {d.probe_max_peak_c} °C</td>
                  <td className="px-2 py-1.5 text-right">
                    <button type="button" onClick={() => onChange({ source: 'replay', dataset: d.name })}
                      className={`rounded px-2 py-1 ${controls.source === 'replay' && controls.dataset === d.name ? 'bg-accent text-white' : 'bg-panel-2 text-ink ring-1 ring-line hover:bg-line'}`}>
                      ▶ 재생
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <input ref={fileRef} type="file" accept=".csv" className="text-xs text-ink-2 file:mr-3 file:rounded file:border-0 file:bg-panel-2 file:px-3 file:py-1.5 file:text-ink" />
          <button type="button" onClick={upload} className="rounded-md bg-panel-2 px-3 py-1.5 text-xs text-ink ring-1 ring-line hover:bg-line">CSV 업로드</button>
          {msg && <span className="text-xs text-ink-2">{msg}</span>}
        </div>
        <p className="mt-2 text-xs text-muted">
          지원 형식: ① 300대 EV 원본 CSV (data_1~6.csv, 자동 변환 — 저장소 github.com/HoraceLiu1010/Multi-modal-SOH-estimation-framework, 전체 300대: ivstskl.changan.com.cn/?p=2697)
          ② 표준 텔레메트리 CSV (열: t_s, current_a, soc_pct, v_01…v_96, tp_01…tp_32). 다른 데이터는 tools/convert_changan.py 를 참고해 변환하세요.
        </p>
      </Card>
    </div>
  );
}
