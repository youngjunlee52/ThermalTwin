import { useEffect, useState } from 'react';
import type { ConnectionStatus, Controls, Dataset, Frame, Mode, Source } from '../types';
import { MODE_LABEL } from '../types';
import { SectionTitle, Segmented, Slider, Toggle } from './ui';

export type Workspace = 'ops' | 'rnd';

interface Props {
  controls: Controls;
  onChange: (patch: Partial<Controls>) => void;
  running: boolean;
  onToggleRunning: () => void;
  onReset: () => void;
  status: ConnectionStatus;
  frame: Frame | null;
  workspace: Workspace;
  onViewVehicle: (id: string) => void;
}

const LOCK_LABEL: Partial<Record<keyof Controls, string>> = {
  mode: '운전 모드', ambient_c: '외기 온도', true_soh: '실제 SOH', time_scale: '배속', channel_width_mm: '채널 폭', channel_depth_mm: '채널 깊이',
};

/** 입력 우선순위: 지금 무엇이 값을 정하는지 (실측 vs 화면) 를 항상 보여줌 */
function InputPriority({ frame: f, source, onSource, onViewVehicle }: {
  frame: Frame | null; source: Source; onSource: (s: Source) => void; onViewVehicle: (id: string) => void;
}) {
  const inp = f?.input;
  const external = source !== 'sim';
  const redirected = !!inp && inp.redirected_samples > 0 && source !== 'ingest';
  if (!external && !redirected) return null;     // 가상 차량: 모든 값을 화면에서 정하므로 안내 불필요
  return (
    <div className="space-y-1.5 rounded-lg bg-panel-2 p-3 text-[11px] leading-relaxed text-ink-2">
      <p className="font-semibold text-ink">실제 측정값이 화면 설정보다 우선합니다</p>
      {external && (
        <p>
          {source === 'ingest' ? '차량이 보내는 값' : '실차 기록'}이 정하는 항목은 화면에서 바꿀 수 없습니다
          ({(inp?.locked ?? []).map((k) => LOCK_LABEL[k] ?? k).join(', ') || '--'}). 아래 '자동 보호 설정'만 바꿀 수 있습니다.
        </p>
      )}
      {redirected && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <span style={{ color: 'var(--color-warning)' }}>▲ 실제 차량 데이터 {inp.redirected_samples}건이 따로('external' 차량) 들어오는 중 — 지금 화면에서 고른 것을 유지하고 있습니다</span>
          <button type="button" onClick={() => onViewVehicle('external')} className="rounded bg-panel px-2 py-0.5 text-ink ring-1 ring-line hover:bg-line">보기</button>
          <button type="button" onClick={() => onSource('ingest')} className="rounded bg-panel px-2 py-0.5 text-ink ring-1 ring-line hover:bg-line">기본 차량으로 받기</button>
        </div>
      )}
    </div>
  );
}

const CONN: Record<ConnectionStatus, { label: string; color: string }> = {
  connecting: { label: '서버 연결 중…', color: 'var(--color-warning)' },
  open: { label: '서버 연결됨 · 1 Hz', color: 'var(--color-good)' },
  closed: { label: '서버 연결 끊김 · 재시도 중', color: 'var(--color-critical)' },
};
const MODES: Mode[] = ['fast_charge', 'highway', 'city', 'aggressive', 'rest'];

export default function Sidebar({ controls: c, onChange, running, onToggleRunning, onReset, status, frame, workspace, onViewVehicle }: Props) {
  const conn = CONN[status];
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  useEffect(() => {
    fetch('/api/datasets').then((r) => r.json()).then(setDatasets).catch(() => setDatasets([]));
  }, [c.source]);
  const sim = c.source === 'sim';

  return (
    <aside className="flex w-full shrink-0 flex-col border-b border-line bg-panel lg:h-screen lg:w-80 lg:border-r lg:border-b-0">
      <div className="border-b border-line px-5 py-4">
        <div className="flex items-center gap-2.5">
          <div className="grid size-8 place-items-center rounded-md bg-accent/20 font-mono text-sm font-bold text-accent">TT</div>
          <div>
            <h1 className="text-sm font-semibold text-ink">ThermalTwin <span className="text-muted">v0.4</span></h1>
            <p className="text-[11px] text-muted">Battery TMS Digital Twin · PINN</p>
          </div>
        </div>
        <div className="mt-3 flex items-center gap-2 rounded-md bg-panel-2 px-3 py-2 text-xs text-ink-2">
          <span className="size-2 rounded-full" style={{ background: conn.color }} />{conn.label}
        </div>
      </div>

      <div className="flex-1 space-y-7 overflow-y-auto px-5 py-5">
        <section className="space-y-3">
          <SectionTitle>① 무엇을 볼까요?</SectionTitle>
          <Segmented<Source> label="데이터 소스" value={c.source} onChange={(v) => onChange({ source: v })}
            options={[{ value: 'sim', label: '가상 차량' }, { value: 'replay', label: '실차 기록' }, { value: 'ingest', label: '실제 차량 연결' }]} />
          <p className="text-[11px] leading-relaxed text-muted">
            {c.source === 'sim' && '컴퓨터 속 전기차입니다. 아래 설정을 바꾸며 실험해 볼 수 있고, 실제 온도를 알고 있어 계산이 맞는지도 확인됩니다.'}
            {c.source === 'replay' && '실제 전기차가 기록한 충전·주행 데이터를 다시 재생합니다 (300대 EV 공개 데이터 중 1대).'}
            {c.source === 'ingest' && '실제 차량(BMS·CAN 게이트웨이)이 보내는 데이터를 받습니다. 데이터가 와야 화면이 시작됩니다.'}
          </p>
          {c.source === 'replay' && (
            <div className="space-y-1.5">
              <label className="text-xs text-ink-2" htmlFor="ds">재생할 기록</label>
              <select id="ds" value={c.dataset} onChange={(e) => onChange({ dataset: e.target.value })}
                className="w-full rounded-md bg-panel-2 px-2 py-2 text-xs text-ink ring-1 ring-line">
                {datasets.map((d) => <option key={d.name} value={d.name}>{d.title} · {d.duration_min}분</option>)}
              </select>
            </div>
          )}
          <InputPriority frame={frame} source={c.source} onSource={(s) => onChange({ source: s })} onViewVehicle={onViewVehicle} />
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={onToggleRunning}
              className={`rounded-md px-3 py-2 text-xs font-semibold ${running ? 'bg-panel-2 text-ink ring-1 ring-line hover:bg-line' : 'bg-accent text-white'}`}>
              {running ? '⏸ 일시정지' : '▶ 실행'}
            </button>
            <button type="button" onClick={onReset} className="rounded-md bg-panel-2 px-3 py-2 text-xs font-semibold text-ink ring-1 ring-line hover:bg-line">
              ↺ 처음부터
            </button>
          </div>
          {c.source !== 'ingest' && (
            <div className="space-y-1.5">
              <span className="text-xs text-ink-2">빠르게 보기 (1초에 N초씩 진행)</span>
              <Segmented label="배속" value={c.time_scale} onChange={(v) => onChange({ time_scale: v })}
                options={[1, 10, 30, 60, 120].map((v) => ({ value: v, label: `×${v}` }))} />
            </div>
          )}
        </section>

        {sim && (
          <>
            <section className="space-y-3">
              <SectionTitle>② 운전 상황</SectionTitle>
              <Segmented label="운전 모드" value={c.mode} onChange={(v) => onChange({ mode: v })}
                options={MODES.map((m) => ({ value: m, label: MODE_LABEL[m] }))} />
              <Slider label="충전 세기" unit="kW" value={c.charge_power_kw} min={50} max={200} step={10}
                hint="클수록 빨리 충전되지만 열이 많이 납니다." onChange={(v) => onChange({ charge_power_kw: v })} />
              <Slider label="바깥 온도" unit="°C" value={c.ambient_c} min={-20} max={45} step={1} onChange={(v) => onChange({ ambient_c: v })} />
            </section>
            <section className="space-y-3">
              <SectionTitle>③ 냉각 장치</SectionTitle>
              <Slider label="냉각수 양" unit="L/min" value={c.coolant_flow_lpm} min={2} max={30} step={1}
                hint={frame?.flow && frame.flow.flow_lpm > c.coolant_flow_lpm + 0.5
                  ? ((frame.flow.flow_requested_lpm ?? 0) > frame.flow.flow_lpm + 0.1
                    ? `자동 보호가 ${frame.flow.flow_requested_lpm?.toFixed(0)} L/min 을 요청했지만, 펌프 한계로 ${frame.flow.flow_lpm.toFixed(1)} L/min 까지 늘었습니다.`
                    : `지금은 자동 보호가 ${frame.flow.flow_lpm.toFixed(1)} L/min 으로 늘려 흘리고 있습니다.`)
                  : frame?.flow && frame.flow.flow_lpm < c.coolant_flow_lpm - 0.1
                    ? `펌프 한계로 실제로는 ${frame.flow.flow_lpm.toFixed(1)} L/min 만 흐릅니다.`
                    : '많을수록 잘 식지만 펌프 전기가 더 듭니다.'}
                onChange={(v) => onChange({ coolant_flow_lpm: v })} />
              <Slider label="냉각수 목표 온도" unit="°C" value={c.coolant_set_c} min={10} max={35} step={1} onChange={(v) => onChange({ coolant_set_c: v })} />
              {workspace === 'rnd' && (
                <>
                  <Slider label="냉각판 채널 폭 w" unit="mm" value={c.channel_width_mm} min={4} max={10} step={0.5} onChange={(v) => onChange({ channel_width_mm: v })} />
                  <Slider label="냉각판 채널 깊이 d" unit="mm" value={c.channel_depth_mm} min={1} max={4} step={0.5} onChange={(v) => onChange({ channel_depth_mm: v })} />
                </>
              )}
            </section>
          </>
        )}

        <section className="space-y-3">
          <SectionTitle>{sim ? '④' : '②'} 자동 보호 설정</SectionTitle>
          {!sim && <Slider label="최대 충전 세기 (이보다 세게 충전하지 않도록 권고)" unit="kW" value={c.charge_power_kw} min={50} max={200} step={10} onChange={(v) => onChange({ charge_power_kw: v })} />}
          <Toggle label="뜨거우면 충전 줄이기 (45 °C 부터)" checked={c.derating} onChange={(v) => onChange({ derating: v })} />
          <div className="space-y-1.5">
            <span className="text-xs text-ink-2">무엇을 보고 판단할까요?</span>
            <Segmented label="판단 기준" value={c.derate_source} onChange={(v) => onChange({ derate_source: v })}
              options={[{ value: 'sensor', label: '겉면 센서' }, { value: 'twin', label: '내부 온도 (추천)' }, { value: 'forecast', label: '10분 뒤 예상' }]} />
          </div>
          <Toggle label="뜨거우면 냉각수 늘리기 (35 °C 부터)" checked={c.auto_pump} onChange={(v) => onChange({ auto_pump: v })} />
          <Toggle label="안전 모드로 고정 (AI 대신 단순 규칙)" checked={c.force_fallback} onChange={(v) => onChange({ force_fallback: v })} />
          <p className="text-[11px] leading-relaxed text-muted">계산을 믿기 어려운 상황이면 이 설정과 상관없이 자동으로 안전 모드가 됩니다.</p>
        </section>

        {workspace === 'rnd' && (
          <section className="space-y-3">
            <SectionTitle>추정기 (R&amp;D)</SectionTitle>
            <Toggle label="셀 전압 96개로 셀 저항 식별" checked={c.use_voltage} onChange={(v) => onChange({ use_voltage: v })} />
            <p className="text-[11px] leading-relaxed text-muted">끄면 온도센서만 사용합니다. 끄고 센서 수를 줄여 보면 왜 전압이 중요한지 알 수 있습니다.</p>
          </section>
        )}

        {sim && (
          <section className="space-y-3 rounded-lg border border-dashed border-axis p-3">
            <SectionTitle>⑤ 가상 차량의 실제 배터리 건강</SectionTitle>
            <Slider label="실제 SOH (프로그램은 모르는 값)" unit="%" value={c.true_soh} min={0.7} max={1} step={0.01} format={(v) => (v * 100).toFixed(0)}
              hint="낡은 배터리일수록 열이 많이 납니다. 프로그램이 이 값을 셀 전압만으로 맞히는지 볼 수 있습니다." onChange={(v) => onChange({ true_soh: v })} />
          </section>
        )}
      </div>
    </aside>
  );
}
