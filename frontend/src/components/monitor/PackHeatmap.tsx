import { useMemo, useState } from 'react';
import type { Cell, Frame, Grid } from '../../types';
import { errorColor, errorGradientCss, heatColor, heatGradientCss, inkOn, seqColor, seqGradientCss } from '../../lib/colors';
import { Card, Segmented } from '../ui';

type View = 'core' | 'core_nn' | 'surf' | 'std' | 'k_cells' | 'true_core' | 'error';
const NOTE: Record<View, string> = {
  core: 'PINN 이 학습한 열전달 파라미터(R_in·C)로 보정한 물리 모델의 코어온도 — 대표 추정값',
  core_nn: 'PINN 신경망 가상센서가 표면온도·발열 이력만으로 추정한 코어온도 (독립 추정, 교차 확인용)',
  surf: '센서 + 학습 공분산 보정으로 추정한 셀 표면온도',
  std: '추정 불확실성 σ (K): 센서에서 멀수록 큼. 센서를 옮기면 바로 바뀝니다',
  k_cells: '셀 전압으로 식별한 셀 저항 배율 (1 = 새 셀 기준) — 높은 셀이 약한 셀 후보',
  true_core: '시뮬레이션 속 실제 코어온도 (검증용, 실차에는 없음)',
  error: '실제 − 트윈 (K). 빨강: 실제가 더 뜨거움(트윈이 놓친 열)',
};

export default function PackHeatmap({ frame: f, onSensors }: { frame: Frame; onSensors: (c: Cell[]) => void }) {
  const [view, setView] = useState<View>('core');
  const [edit, setEdit] = useState(false);
  const [hover, setHover] = useState<Cell | null>(null);
  const sim = f.source === 'sim';
  const views: { value: View; label: string }[] = [
    { value: 'core', label: '코어 (하이브리드)' }, { value: 'core_nn', label: '코어 (PINN 신경망)' },
    { value: 'surf', label: '표면' }, { value: 'std', label: '불확실성' }, { value: 'k_cells', label: '셀 저항' },
    ...(sim ? [{ value: 'true_core' as View, label: '실제 코어' }, { value: 'error' as View, label: '오차' }] : []),
  ];
  const v: View = !sim && (view === 'true_core' || view === 'error') ? 'core' : view;

  const grid: Grid = useMemo(() => {
    if (v === 'error' && f.field.true_core) return f.field.true_core.map((row, r) => row.map((x, c) => x - f.field.core[r][c]));
    if (v === 'true_core' && f.field.true_core) return f.field.true_core;
    return f.field[v as 'core' | 'core_nn' | 'surf' | 'std' | 'k_cells'];
  }, [f, v]);

  const key = (r: number, c: number) => `${r}-${c}`;
  const sensorSet = new Set(f.sensors.map(([r, c]) => key(r, c)));
  const isolated = new Set((f.faults?.bad_sensors ?? []).map(([r, c]) => key(r, c)));
  const localOnly = new Set((f.faults?.local_sensors ?? []).map(([r, c]) => key(r, c)));
  const avail = new Set((f.replay?.available_probes ?? []).map(([r, c]) => key(r, c)));
  const weak = new Set(f.twin.weak_cells.slice(0, 3).map(([r, c]) => key(r, c)));
  const flat = grid.flat();
  const lo0 = Math.min(...flat);
  const hi0 = Math.max(...flat);
  const isTemp = v === 'core' || v === 'core_nn' || v === 'surf' || v === 'true_core';
  const tMin = Math.floor(lo0);
  const tMax = Math.max(Math.ceil(hi0), tMin + 4);
  const errSpan = Math.max(0.5, Math.ceil(Math.max(Math.abs(lo0), Math.abs(hi0)) * 2) / 2);
  const color = (x: number) => (v === 'error' ? errorColor(x, errSpan)
    : isTemp ? heatColor(x, tMin, tMax) : seqColor(x, v === 'k_cells' ? Math.min(lo0, 0.8) : 0, v === 'k_cells' ? Math.max(hi0, 1.6) : Math.max(hi0, 0.5)));
  const maxIdx = flat.indexOf(hi0);
  const hot: Cell = [Math.floor(maxIdx / 12), maxIdx % 12];

  const toggle = (r: number, c: number) => {
    if (!edit || f.source === 'ingest') return;
    if (f.source === 'replay' && !avail.has(key(r, c))) return;
    const cur = f.sensors.filter(([a, b]) => !(a === r && b === c));
    onSensors(cur.length === f.sensors.length ? [...f.sensors, [r, c]] : cur);
  };

  const legend = v === 'error' ? [`-${errSpan}`, errorGradientCss(), `+${errSpan} K`]
    : isTemp ? [String(tMin), heatGradientCss(), `${tMax} °C`]
      : [v === 'k_cells' ? Math.min(lo0, 0.8).toFixed(2) : '0', seqGradientCss(), v === 'k_cells' ? `${Math.max(hi0, 1.6).toFixed(2)} ×` : `${Math.max(hi0, 0.5).toFixed(2)} K`];

  return (
    <Card title="배터리팩 (8 모듈 × 12 셀)"
      right={<div className="w-full xl:w-auto"><Segmented label="표시 항목" value={v} onChange={setView} options={views} /></div>}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted">{NOTE[v]}</p>
        {f.source !== 'ingest' && (
          <button type="button" onClick={() => setEdit(!edit)}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold ${edit ? 'bg-accent text-white' : 'bg-panel-2 text-ink ring-1 ring-line hover:bg-line'}`}>
            {edit ? '✓ 센서 편집 끝내기' : '✎ 센서 위치 편집'}
          </button>
        )}
      </div>
      {edit && (
        <p className="mb-2 rounded-md bg-panel-2 px-3 py-2 text-xs text-ink-2">
          셀을 눌러 온도센서를 추가·제거하세요. {f.source === 'replay' ? '실차 재생에서는 실제 프로브가 있는 위치(빈 원)만 고를 수 있고, 고르지 않은 프로브는 검증에 쓰입니다.' : '트윈의 보정 가중치와 불확실성 지도가 즉시 다시 계산됩니다.'}
        </p>
      )}
      <div className="flex items-stretch gap-2">
        <div className="flex flex-col justify-around pr-1 font-mono text-[10px] text-muted">
          {grid.map((_, r) => <span key={r}>M{r + 1}</span>)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="grid gap-[2px]" style={{ gridTemplateColumns: 'repeat(12, minmax(0, 1fr))' }} onMouseLeave={() => setHover(null)}>
            {grid.map((row, r) => row.map((x, c) => {
              const k = key(r, c);
              const isS = sensorSet.has(k);
              const isHot = r === hot[0] && c === hot[1] && v !== 'error';
              return (
                <button type="button" key={k} onMouseEnter={() => setHover([r, c])} onClick={() => toggle(r, c)}
                  className={`relative aspect-[4/3] rounded-[4px] ${edit ? 'cursor-pointer' : 'cursor-default'}`}
                  style={{ background: color(x), outline: isHot ? '2px solid #f0efec' : 'none', outlineOffset: -2 }}
                  aria-label={`M${r + 1}-C${c + 1} ${x.toFixed(2)}${isS ? ' 센서' : ''}`}>
                  {isS && !isolated.has(k) && !localOnly.has(k) && <span className="absolute top-1 right-1 size-2 rounded-full bg-white ring-1 ring-black/60" />}
                  {isolated.has(k) && <span className="absolute top-0 right-0.5 text-[11px] font-bold leading-none" style={{ color: 'var(--color-critical)' }} title="고장 의심 → 보정에서 제외">✕</span>}
                  {localOnly.has(k) && <span className="absolute top-0 right-1 text-[11px] font-bold leading-none" style={{ color: 'var(--color-serious)' }} title="주변보다 뜨거움 → 그 셀에만 반영, 현장 확인">!</span>}
                  {!isS && avail.has(k) && <span className="absolute top-1 right-1 size-2 rounded-full ring-1 ring-white/80" />}
                  {v === 'k_cells' && weak.has(k) && <span className="absolute bottom-0.5 left-1 text-[10px] font-bold text-white">!</span>}
                  {isHot && <span className="absolute inset-0 grid place-items-center font-mono text-[10px] font-semibold"
                    style={{ color: isTemp ? inkOn(x, tMin, tMax) : '#f0efec' }}>{x.toFixed(1)}</span>}
                </button>
              );
            }))}
          </div>
          <div className="mt-1 flex items-center gap-2 text-[10px] text-muted">
            <span>냉각수 입구</span><span className="h-px flex-1 bg-axis" /><span>흐름 →</span><span className="h-px flex-1 bg-axis" /><span>출구</span>
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted">
          <span className="font-mono">{legend[0]}</span>
          <span className="h-2.5 w-36 rounded-sm" style={{ background: legend[1] }} />
          <span className="font-mono">{legend[2]}</span>
          <span className="ml-2 inline-flex items-center gap-1"><span className="size-2 rounded-full bg-white" /> 사용 중 센서 ({f.sensors.length})</span>
          {f.replay && <span className="inline-flex items-center gap-1"><span className="size-2 rounded-full ring-1 ring-white/80" /> 검증용 프로브</span>}
          {isolated.size > 0 && <span style={{ color: 'var(--color-critical)' }}>✕ 고장 격리 {isolated.size}</span>}
          {localOnly.size > 0 && <span style={{ color: 'var(--color-serious)' }}>! 혼자 뜨거움(현장 확인) {localOnly.size}</span>}
        </div>
        <div className="min-h-[2.5rem] font-mono text-[11px] leading-relaxed text-ink-2">
          {hover ? (() => {
            const [r, c] = hover;
            const probe = f.replay?.probe_values[key(r, c)];
            return (
              <>
                <b className="text-ink">M{r + 1}-C{c + 1}</b>{sensorSet.has(key(r, c)) ? ' · 센서' : ''} · 저항 ×{f.field.k_cells[r][c].toFixed(2)} · σ {f.field.std[r][c].toFixed(2)} K<br />
                코어 {f.field.core[r][c].toFixed(1)} / 신경망 {f.field.core_nn[r][c].toFixed(1)} · 표면 {f.field.surf[r][c].toFixed(1)} °C
                {f.field.true_core && <> · 실제 코어 {f.field.true_core[r][c].toFixed(1)}</>}
                {probe !== undefined && <> · 실측 프로브 {probe.toFixed(0)} °C</>}
              </>
            );
          })() : <span className="text-muted">셀에 마우스를 올리면 값이 표시됩니다</span>}
        </div>
      </div>
    </Card>
  );
}
