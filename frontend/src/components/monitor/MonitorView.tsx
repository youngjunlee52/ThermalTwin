import type { ReactNode } from 'react';
import type { Cell, ConnectionStatus, Frame } from '../../types';
import { DELTA_T_TARGET_K, T_CORE_DANGER_C, T_CORE_WARNING_C } from '../../types';
import type { HistoryPoint } from '../../hooks/useSimulation';
import { Term } from '../ui';
import ControlPanel from './ControlPanel';
import PackHeatmap from './PackHeatmap';
import StatusHero from './StatusHero';
import TrendCharts from './TrendCharts';
import TwinPanel from './TwinPanel';

interface Props {
  latest: Frame | null; history: HistoryPoint[]; status: ConnectionStatus; onSensors: (c: Cell[]) => void;
  onForceFallback?: (v: boolean) => void;   // 다른 차량을 보는 중이면 없음 (화면 설정은 기본 차량에만 적용)
}

function Kpi({ label, value, unit, sub, tone }: { label: ReactNode; value: string; unit?: string; sub?: ReactNode; tone?: 'warning' | 'danger' }) {
  const ring = tone === 'danger' ? 'border-critical animate-danger-blink' : tone === 'warning' ? 'border-warning' : 'border-line';
  return (
    <div className={`rounded-xl border bg-panel p-3.5 ${ring}`}>
      <p className="text-xs font-medium text-ink-2">{label}</p>
      <p className="mt-1.5 flex items-baseline gap-1"><span className="text-2xl font-semibold text-ink">{value}</span>{unit && <span className="text-xs text-muted">{unit}</span>}</p>
      {sub && <div className="mt-1 text-[11px] leading-snug text-muted">{sub}</div>}
    </div>
  );
}

function formatHours(h: number) {
  if (h >= 24 * 365) return '1년 이상';
  if (h >= 48) return `${Math.round(h / 24)}일`;
  if (h >= 1) return `${h.toFixed(1)}시간`;
  return `${Math.round(h * 60)}분`;
}

export default function MonitorView({ latest: f, history, status, onSensors, onForceFallback }: Props) {
  if (!f) {
    return (
      <div className="grid h-80 place-items-center rounded-xl border border-dashed border-axis p-6 text-center text-sm text-muted">
        {status === 'closed' ? <p>서버를 찾는 중입니다… <b className="text-ink-2">ThermalTwin Server</b> 검은 창이 켜져 있는지 확인하세요.</p>
          : status === 'open' ? <p>데이터를 기다리는 중… ('외부 수신'을 골랐다면 차량·게이트웨이가 데이터를 보내야 시작됩니다)</p> : <p>연결 중…</p>}
      </div>
    );
  }
  const tone = f.kpi.core_max_c >= T_CORE_DANGER_C ? 'danger' : f.kpi.core_max_c >= T_CORE_WARNING_C ? 'warning' : undefined;
  const fc = f.forecast;
  const ra = f.runaway;

  return (
    <div className="space-y-4">
      <StatusHero frame={f} />

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Kpi label={<>가장 뜨거운 셀 <Term k="코어온도">내부 온도</Term></>} value={f.kpi.core_max_c.toFixed(1)} unit="°C" tone={tone}
          sub={<>겉면 센서 최고 {f.kpi.sensor_max_c?.toFixed(1) ?? '--'} °C · 두 계산(<Term k="하이브리드" /> {f.kpi.core_hybrid_max_c.toFixed(1)} / <Term k="PINN" /> {f.kpi.core_nn_max_c.toFixed(1)}) 중 높은 값</>} />
        <Kpi label={`${fc.horizon_min}분 뒤 예상 (지금처럼 계속하면)`} value={fc.peak_c?.toFixed(1) ?? '--'} unit="°C"
          tone={fc.peak_c && fc.peak_c >= T_CORE_WARNING_C ? 'warning' : undefined}
          sub={fc.minutes_to_warning ? <span style={{ color: 'var(--color-warning)' }}>▲ 약 {fc.minutes_to_warning}분 뒤 주의 온도</span> : '지금 전류가 이어진다고 가정'} />
        <Kpi label={<><Term k="열폭주" />까지 남은 여유</>} value={ra.margin_to_onset_k.toFixed(0)} unit="°C"
          tone={ra.level === 'Danger' ? 'danger' : ra.level === 'Warning' ? 'warning' : undefined}
          sub={<>{ra.onset_c.toFixed(0)} °C 부터 위험 · 냉각이 끊기면 약 {formatHours(ra.adiabatic_hours)} 뒤{!ra.cooled_stable && <b style={{ color: 'var(--color-critical)' }}> · 냉각으로도 못 막는 온도</b>}</>} />
        <Kpi label={<>셀 사이 온도 차이 (<Term k="ΔT" />)</>} value={f.kpi.delta_t_k.toFixed(1)} unit="°C"
          sub={<>목표 {DELTA_T_TARGET_K} °C 이내 {f.kpi.delta_t_k < DELTA_T_TARGET_K ? '✓ 만족' : '✕ 넘음'}</>} />
        <Kpi label={<>충전 상태 (<Term k="SOC" />)</>} value={f.current_soc.toFixed(1)} unit="%"
          sub={<>{f.current_a < -5 ? '충전' : f.current_a > 5 ? '방전' : '대기'} {Math.abs(f.current_a).toFixed(0)} A (<Term k="C-rate">{f.c_rate.toFixed(2)} C</Term>) · 발열 {f.heat_kw.toFixed(2)} kW · <Term k="냉각수" /> {f.flow ? `${f.flow.flow_lpm.toFixed(0)} L/min` : `${f.coolant_in_c?.toFixed(1)} °C`}</>} />
      </section>

      <ControlPanel frame={f} onForceFallback={onForceFallback} />

      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <PackHeatmap frame={f} onSensors={onSensors} />
        <TwinPanel frame={f} history={history} />
      </div>
      <TrendCharts history={history} frame={f} />
    </div>
  );
}
