import { useCallback, useEffect, useRef, useState } from 'react';
import Sidebar, { type Workspace } from './components/Sidebar';
import MonitorView from './components/monitor/MonitorView';
import SensorView from './components/sensors/SensorView';
import PinnView from './components/pinn/PinnView';
import DataView from './components/data/DataView';
import DesignView from './components/design/DesignView';
import AboutView from './components/about/AboutView';
import Guide from './components/Guide';
import { RISK_META, RiskBadge, Term } from './components/ui';
import { useSimulation } from './hooks/useSimulation';
import { useModels } from './hooks/useModels';
import { DEFAULT_CONTROLS, MODE_LABEL, SOURCE_LABEL, type Cell, type Controls } from './types';

type Tab = 'monitor' | 'sensors' | 'pinn' | 'data' | 'design' | 'about';
// 운용 모드: 현장 모니터링·제어만 / R&D 모드: 모델 분석·설계·근거
const TABS: Record<Workspace, { id: Tab; label: string }[]> = {
  ops: [
    { id: 'monitor', label: '배터리 상태 · 제어' },
    { id: 'data', label: '차량 · 연동' },
  ],
  rnd: [
    { id: 'monitor', label: '실시간 모니터' },
    { id: 'sensors', label: '센서 배치 분석' },
    { id: 'pinn', label: 'PINN 모델' },
    { id: 'design', label: '냉각판 설계' },
    { id: 'about', label: '모델 · 출처 · 한계' },
  ],
};
const WS_KEY = 'thermaltwin.workspace';
const GUIDE_KEY = 'thermaltwin.guide_seen';

function loadGuideSeen(): boolean {
  try {
    return localStorage.getItem(GUIDE_KEY) === '1';
  } catch {
    return false;
  }
}

function loadWorkspace(): Workspace {
  try {
    return localStorage.getItem(WS_KEY) === 'rnd' ? 'rnd' : 'ops';
  } catch {
    return 'ops';
  }
}

function formatSimTime(s: number) {
  const m = Math.floor(s / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
}

export default function App() {
  const [controls, setControls] = useState<Controls>(DEFAULT_CONTROLS);
  const [tab, setTab] = useState<Tab>('monitor');
  const [workspace, setWorkspace] = useState<Workspace>(loadWorkspace);
  const [guide, setGuide] = useState<boolean>(() => !loadGuideSeen());
  const closeGuide = () => {
    setGuide(false);
    try {
      localStorage.setItem(GUIDE_KEY, '1');
    } catch {
      /* 저장 불가여도 동작 */
    }
  };
  const lastEdit = useRef(0);
  const switchWorkspace = (w: Workspace) => {
    setWorkspace(w);
    try {
      localStorage.setItem(WS_KEY, w);
    } catch {
      /* 저장 불가(사생활 보호 모드 등)여도 동작 */
    }
    if (!TABS[w].some((t) => t.id === tab)) setTab('monitor');
  };
  const { latest, history, status, send, clearHistory } = useSimulation();
  const models = useModels();

  // 서버 엔진의 설정을 화면에 반영 (다른 창·게이트웨이가 바꾼 경우 포함). 사용자가 막 조작한 직후는 제외
  useEffect(() => {
    // 다른 차량을 보는 중에는 그 차량 설정이 기본 차량 사이드바에 섞이지 않도록 동기화하지 않음
    if (latest && latest.vehicle_id === 'default' && Date.now() - lastEdit.current > 1500) setControls(latest.controls);
  }, [latest]);

  const change = useCallback((patch: Partial<Controls>) => {
    lastEdit.current = Date.now();
    setControls((prev) => ({ ...prev, ...patch }));
    send({ type: 'control', ...patch });
    if (patch.source || patch.dataset) clearHistory();
  }, [send, clearHistory]);

  const setSensors = useCallback((cells: Cell[]) => send({ type: 'sensors', cells }), [send]);
  const reset = () => {
    send({ type: 'reset', ...controls });
    clearHistory();
  };
  const running = latest?.running ?? true;
  const toggleRunning = () => send({ type: running ? 'pause' : 'resume' });
  const vehicles = latest?.fleet?.vehicles ?? [];
  const viewVehicle = (vehicle_id: string) => {
    send({ type: 'view_vehicle', vehicle_id });
    clearHistory();
  };
  const viewingOther = latest !== null && latest.vehicle_id !== 'default';

  return (
    <div className="flex min-h-screen flex-col bg-page text-ink lg:h-screen lg:flex-row lg:overflow-hidden">
      <Sidebar controls={controls} onChange={change} running={running} onToggleRunning={toggleRunning}
        onReset={reset} status={status} frame={latest} workspace={workspace} onViewVehicle={viewVehicle} />
      <main className="flex min-w-0 flex-1 flex-col lg:overflow-y-auto">
        <header className="sticky top-0 z-20 border-b border-line bg-page/90 px-4 backdrop-blur sm:px-6">
          <div className="flex flex-wrap items-center justify-between gap-3 py-3">
            <div>
              <h2 className="text-base font-semibold">전기차 배터리 <Term k="디지털트윈">디지털 트윈</Term></h2>
              <p className="text-xs text-muted">
                {viewingOther && <b className="text-ink-2">차량 {latest.vehicle_id} · </b>}
                {latest ? SOURCE_LABEL[latest.source] : '--'} · 셀 96개 배터리팩 · 온도센서 {latest?.kpi.n_sensors ?? '--'}개
                {viewingOther && ' · 왼쪽 설정은 기본 차량에만 적용됩니다'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-3 font-mono text-xs text-ink-2">
              <button type="button" onClick={() => setGuide(true)}
                className="rounded-md bg-panel-2 px-2.5 py-1 font-sans text-xs font-semibold text-ink ring-1 ring-line hover:bg-line">? 도움말</button>
              <div role="radiogroup" aria-label="화면 모드" className="flex rounded-lg bg-panel-2 p-0.5 font-sans">
                {([['ops', '운용'], ['rnd', 'R&D']] as const).map(([w, label]) => (
                  <button key={w} type="button" role="radio" aria-checked={workspace === w} onClick={() => switchWorkspace(w)}
                    className={`rounded-md px-2.5 py-1 text-xs font-semibold ${workspace === w ? 'bg-accent text-white' : 'text-ink-2 hover:bg-line'}`}>
                    {label}
                  </button>
                ))}
              </div>
              {vehicles.length > 1 && (
                <label className="flex items-center gap-1.5 font-sans">
                  <span className="text-muted">차량</span>
                  <select value={latest?.vehicle_id ?? 'default'} onChange={(e) => viewVehicle(e.target.value)}
                    className="rounded-md bg-panel-2 px-2 py-1 text-xs text-ink ring-1 ring-line">
                    {vehicles.map((v) => (
                      <option key={v.vehicle_id} value={v.vehicle_id}>
                        {v.vehicle_id === 'default' ? '기본 (화면 제어)' : v.vehicle_id}{v.risk_status && v.risk_status !== 'Safe' ? ` · ${RISK_META[v.risk_status].label}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <span className="font-sans">경과 <span className="font-mono">{latest ? formatSimTime(latest.t_sim_s) : '--'}</span></span>
              <span>{latest ? MODE_LABEL[latest.mode] : '--'}</span>
              {latest && <RiskBadge status={latest.risk_status} />}
            </div>
          </div>
          <nav className="-mb-px flex gap-1 overflow-x-auto" role="tablist">
            {TABS[workspace].map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
                className={`border-b-2 px-3 py-2 text-sm whitespace-nowrap transition-colors ${tab === t.id
                  ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink-2'}`}>
                {t.label}
              </button>
            ))}
          </nav>
        </header>
        <div className="flex-1 space-y-4 p-4 sm:p-6">
          {guide && <Guide onClose={closeGuide} />}
          {tab === 'monitor' && <MonitorView latest={latest} history={history} status={status} onSensors={setSensors}
            onForceFallback={viewingOther ? undefined : (v) => change({ force_fallback: v })} />}
          {tab === 'sensors' && <SensorView models={models} latest={latest} onSensors={setSensors} />}
          {tab === 'pinn' && <PinnView models={models} latest={latest} />}
          {tab === 'data' && <DataView latest={latest} controls={controls} onChange={change} onViewVehicle={viewVehicle} />}
          {tab === 'design' && <DesignView controls={controls} onApply={change} />}
          {tab === 'about' && <AboutView />}
        </div>
      </main>
    </div>
  );
}
