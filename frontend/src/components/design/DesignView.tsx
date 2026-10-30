import { useMemo, useState } from 'react';
import {
  CartesianGrid, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts';
import type { Controls, SweepBrief, SweepPoint, SweepResult } from '../../types';
import { CHROME, SERIES } from '../../lib/colors';
import { Card, Segmented } from '../ui';

type Metric = 'tmax' | 'dt';
const METRIC: Record<Metric, { key: 'peak_core_c' | 'peak_delta_t_k'; label: string; unit: string }> = {
  tmax: { key: 'peak_core_c', label: '최고 코어온도', unit: '°C' },
  dt: { key: 'peak_delta_t_k', label: '셀 간 최대 온도편차', unit: 'K' },
};
const WIDTH_MAX = 10;
const DEPTH_MAX = 4;

function PointTip({ active, payload, metric }: { active?: boolean; payload?: { payload: SweepPoint }[]; metric: Metric }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  const m = METRIC[metric];
  return (
    <div className="rounded-lg border border-axis bg-panel px-3 py-2 font-mono text-xs text-ink-2">
      <div className="text-ink">w {p.width_mm} mm · d {p.depth_mm} mm · {p.flow_lpm} L/min</div>
      <div>{m.label} {p[m.key].toFixed(2)} {m.unit}</div>
      <div>펌프 {p.pump_w.toFixed(1)} W (회전수 {(p.pump_speed * 100).toFixed(0)} %, 효율 {(p.pump_eta * 100).toFixed(0)} %)</div>
      <div>Δp 판 {p.dp_kpa.toFixed(1)} + 루프 {(p.dp_total_kpa - p.dp_kpa).toFixed(1)} kPa · Re {p.reynolds.toFixed(0)}</div>
      {!p.feasible && <div style={{ color: 'var(--color-critical)' }}>운전 불가: {p.infeasible_reason}</div>}
    </div>
  );
}

const design = (b: SweepBrief | null) => (b ? `w ${b.width_mm} · d ${b.depth_mm} mm · ${b.flow_lpm} L/min` : '--');

export default function DesignView({ controls, onApply }: { controls: Controls; onApply: (p: Partial<Controls>) => void }) {
  const [result, setResult] = useState<SweepResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [metric, setMetric] = useState<Metric>('tmax');
  const m = METRIC[metric];

  const run = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/design-sweep', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          charge_power_kw: controls.charge_power_kw, ambient_c: controls.ambient_c,
          coolant_set_c: controls.coolant_set_c, soh: controls.true_soh,
          channel_width_mm: controls.channel_width_mm, channel_depth_mm: controls.channel_depth_mm,
          coolant_flow_lpm: controls.coolant_flow_lpm,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setResult((await res.json()) as SweepResult);
    } catch (e) {
      setError(`계산 요청 실패: ${String(e)} — 백엔드 서버가 켜져 있는지 확인하세요.`);
    } finally {
      setLoading(false);
    }
  };

  const groups = useMemo(() => {
    if (!result) return null;
    const rec = result.recommend_3d ? result.points[result.recommend_3d.id] : null;
    const old = result.old_recommend ? result.points[result.old_recommend.id] : null;
    const current = result.current_id != null ? result.points[result.current_id] : null;
    const pareto = result.points.filter((p) => p.pareto_3d).sort((a, b) => a.pump_w - b.pump_w);
    const special = new Set([rec?.id, current?.id, old?.id]);
    const feasible = result.points.filter((p) => p.feasible && !p.pareto_3d && !special.has(p.id));
    const infeasible = result.points.filter((p) => !p.feasible && !special.has(p.id));
    return { rec, old, current, pareto, feasible, infeasible };
  }, [result]);

  const { logTicks, logDomain } = useMemo(() => {
    const ws = result ? result.points.filter((p) => p.feasible).map((p) => p.pump_w) : [1];
    const lo = Math.min(...ws);
    const hi = Math.max(...ws);
    const all = [0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000, 3000, 10000];
    const start = [...all].reverse().find((t) => t <= lo) ?? 0.1;
    const end = all.find((t) => t >= hi) ?? 10000;
    return { logTicks: all.filter((t) => t >= start && t <= end), logDomain: [start, end] as [number, number] };
  }, [result]);

  const rec = result?.recommend_3d ?? null;
  const atEdge = rec !== null && (rec.width_mm >= WIDTH_MAX || rec.depth_mm >= DEPTH_MAX);

  return (
    <div className="space-y-4">
      <Card title="냉각판 설계 공간 탐색 (다목적 최적화 · 펌프 운전 한계 반영)">
        <p className="text-sm leading-relaxed text-ink-2">
          채널 폭 w (4–10 mm) × 깊이 d (1–4 mm) × 유량 (5–25 L/min) 245가지 조합으로 SOC 10→80 % 급속충전을 각각 시뮬레이션합니다.
          <b className="text-ink"> 펌프가 실제로 낼 수 있는 설계만</b> 남긴 뒤 <b className="text-ink">최고온도 · 온도편차 · 펌프 전기 소비</b> 세 가지를 함께 줄이는 파레토 설계를 찾습니다.
        </p>
        <p className="mt-2 text-xs text-muted">
          조건: 충전 {controls.charge_power_kw} kW · 외기 {controls.ambient_c} °C · 냉각수 {controls.coolant_set_c} °C ·
          SOH {(controls.true_soh * 100).toFixed(0)} % (사이드바에서 바꾸고 다시 실행)
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button type="button" onClick={run} disabled={loading}
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-white hover:brightness-110 disabled:opacity-50">
            {loading ? '계산 중…' : result ? '다시 실행' : '설계 탐색 실행'}
          </button>
          {result && (
            <span className="font-mono text-xs text-muted">
              {result.n_cases}개 중 운전 가능 {result.n_feasible}개 · {result.elapsed_s}초 · 충전 시간 {result.charge_time_min}분
            </span>
          )}
          {error && <span className="text-xs" style={{ color: 'var(--color-critical)' }}>{error}</span>}
        </div>
      </Card>

      {result && groups && (
        <>
          <div className="grid gap-4 xl:grid-cols-3">
            <Card title="추천 설계 (3목적 무릎점)" className="xl:col-span-2">
              {rec ? (
                <div className="space-y-2 text-sm text-ink-2">
                  <p className="font-mono text-lg text-ink">{design(rec)}</p>
                  <p>최고 코어 <b className="text-ink">{rec.peak_core_c.toFixed(1)} °C</b> · 온도편차 <b className="text-ink">{rec.peak_delta_t_k.toFixed(1)} K</b>
                    {rec.peak_delta_t_k <= result.dt_target_k ? ' ✓' : ' ✕'} (목표 {result.dt_target_k} K) · 펌프 <b className="text-ink">{rec.pump_w.toFixed(1)} W</b> (회전수 {(rec.pump_speed * 100).toFixed(0)} %) · 총 Δp {rec.dp_total_kpa.toFixed(1)} kPa</p>
                  {result.old_recommend && (
                    <p className="text-xs text-muted">이전 방식(고정 효율 35 %, 루프 손실·운전 한계 무시) 추천: {design(result.old_recommend)} — 펌프 {result.old_recommend.pump_w.toFixed(1)} W, 최고 {result.old_recommend.peak_core_c.toFixed(1)} °C</p>
                  )}
                  {result.n_dt_ok_feasible === 0 && (
                    <p className="text-xs" style={{ color: 'var(--color-warning)' }}>▲ 이 조건에서는 운전 가능한 설계 중 온도편차 {result.dt_target_k} K 를 만족하는 것이 없습니다 — 냉각판 형상만으로는 한계 (냉각수 온도·유량 분배·충전 출력 조정 필요)</p>
                  )}
                  {atEdge && (
                    <p className="text-xs" style={{ color: 'var(--color-warning)' }}>▲ 추천이 탐색 범위 끝(폭 {WIDTH_MAX} mm 또는 깊이 {DEPTH_MAX} mm)에 있습니다. 냉각판 두께·셀 폭이 허용하면 더 넓은 범위를 탐색할 가치가 있습니다.</p>
                  )}
                  <button type="button" className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white"
                    onClick={() => onApply({ channel_width_mm: rec.width_mm, channel_depth_mm: rec.depth_mm, coolant_flow_lpm: rec.flow_lpm })}>
                    실시간 모니터에 적용
                  </button>
                </div>
              ) : <p className="text-sm text-muted">운전 가능한 설계가 없습니다.</p>}
            </Card>
            <Card title="펌프·루프 모델 (가정값)">
              <ul className="space-y-1 text-xs text-ink-2">
                <li>최대 양정 {result.pump_model.h0_kpa} kPa · 최대 유량 {result.pump_model.q_max_lpm} L/min (12 V 전동 워터펌프 급)</li>
                <li>회전수 조절(상사법칙), 수력효율 최고 {(result.pump_model.eta_hyd_peak * 100).toFixed(0)} % · 모터 60–85 %</li>
                <li>냉각판 밖 루프 손실 {result.pump_model.loop_dp_kpa_at_20lpm} kPa @ 20 L/min (칠러·호스·커넥터)</li>
                <li>냉각판 허용 압력 {result.pump_model.plate_dp_limit_kpa} kPa</li>
                <li>압력강하 = 직관 마찰 + 180° 굽힘 + 입·출구 손실 + 루프</li>
              </ul>
              <p className="mt-2 text-[11px] text-muted">실제 펌프 사양서(성능곡선·효율)로 backend/pump.py 의 값을 바꾸면 그대로 반영됩니다.</p>
            </Card>
          </div>

          <Card title={`${m.label} vs 펌프 전기 소비`}
            right={<Segmented label="세로축" value={metric} onChange={setMetric}
              options={[{ value: 'tmax', label: '최고 코어온도' }, { value: 'dt', label: '온도편차 ΔT' }]} />}>
            <div className="h-96">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 16, bottom: 18, left: 4 }}>
                  <CartesianGrid stroke={CHROME.grid} />
                  <XAxis type="number" dataKey="pump_w" scale="log" domain={logDomain} ticks={logTicks} allowDataOverflow
                    stroke={CHROME.axis} tick={{ fill: CHROME.muted, fontSize: 11 }}
                    tickFormatter={(v: number) => (v >= 1 ? String(v) : v.toFixed(1))}
                    label={{ value: '펌프 전기 소비 (W, 로그 축) — 운전 불가 설계는 오른쪽 밖', position: 'insideBottom', offset: -10, fill: CHROME.muted, fontSize: 11 }} />
                  <YAxis type="number" dataKey={m.key} domain={['auto', 'auto']} stroke={CHROME.axis}
                    tick={{ fill: CHROME.muted, fontSize: 11 }} width={48} unit={m.unit === '°C' ? '°' : 'K'} />
                  <ZAxis range={[36, 36]} />
                  <Tooltip content={<PointTip metric={metric} />} cursor={{ stroke: CHROME.axis }} />
                  <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12, color: CHROME.ink2 }} />
                  <Scatter name="운전 불가 (펌프·압력 한계)" data={groups.infeasible} fill="none" stroke={CHROME.muted} strokeOpacity={0.5} isAnimationActive={false} />
                  <Scatter name="운전 가능" data={groups.feasible} fill={CHROME.muted} fillOpacity={0.55} isAnimationActive={false} />
                  <Scatter name="3목적 파레토" data={groups.pareto} fill={SERIES.blue} isAnimationActive={false} />
                  {groups.current && <Scatter name="현재 설계" data={[groups.current]} fill={SERIES.orange} shape="diamond" isAnimationActive={false} legendType="diamond" />}
                  {groups.old && <Scatter name="이전 방식 추천" data={[groups.old]} fill={CHROME.ink2} shape="triangle" isAnimationActive={false} legendType="triangle" />}
                  {groups.rec && <Scatter name="추천" data={[groups.rec]} fill={SERIES.aqua} shape="star" isAnimationActive={false} legendType="star" />}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-2 text-xs leading-relaxed text-muted">
              왼쪽 아래가 좋은 설계입니다. 파레토 설계는 최고온도·온도편차·펌프 동력 중 하나를 좋게 하면 다른 것이 나빠지는 설계들이고,
              추천은 세 값을 정규화(펌프 동력은 로그)했을 때 이상점에 가장 가까운 '무릎점'입니다.
            </p>
          </Card>

          <Card title={`3목적 파레토 설계 ${groups.pareto.length}개 — 압력강하 내역`}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-left text-xs">
                <thead className="text-muted">
                  <tr className="border-b border-line">
                    {['폭 w', '깊이 d', '유량', '최고 코어', 'ΔT', '펌프', '회전수', 'Δp 마찰', '굽힘', '입출구', '루프', 'Re', ''].map((h) => (
                      <th key={h} className="px-2 py-2 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="font-mono tabular-nums text-ink-2">
                  {groups.pareto.map((p) => (
                    <tr key={p.id} className={`border-b border-line/60 ${p.id === rec?.id ? 'bg-panel-2' : ''}`}>
                      <td className="px-2 py-1.5">{p.width_mm}</td>
                      <td className="px-2 py-1.5">{p.depth_mm}</td>
                      <td className="px-2 py-1.5">{p.flow_lpm}</td>
                      <td className="px-2 py-1.5">{p.peak_core_c.toFixed(1)}</td>
                      <td className="px-2 py-1.5" style={{ color: p.dt_ok ? 'var(--color-good)' : undefined }}>{p.peak_delta_t_k.toFixed(1)}</td>
                      <td className="px-2 py-1.5">{p.pump_w.toFixed(1)} W</td>
                      <td className="px-2 py-1.5">{(p.pump_speed * 100).toFixed(0)} %</td>
                      <td className="px-2 py-1.5">{p.dp_friction_kpa.toFixed(1)}</td>
                      <td className="px-2 py-1.5">{p.dp_bend_kpa.toFixed(1)}</td>
                      <td className="px-2 py-1.5">{p.dp_port_kpa.toFixed(2)}</td>
                      <td className="px-2 py-1.5">{(p.dp_total_kpa - p.dp_kpa).toFixed(1)}</td>
                      <td className="px-2 py-1.5">{p.reynolds.toFixed(0)}</td>
                      <td className="px-2 py-1.5 text-right">
                        {p.id === rec?.id && <span className="mr-2 font-sans" style={{ color: SERIES.aqua }}>★ 추천</span>}
                        <button type="button" className="rounded bg-panel-2 px-2 py-1 font-sans text-ink ring-1 ring-line hover:bg-line"
                          onClick={() => onApply({ channel_width_mm: p.width_mm, channel_depth_mm: p.depth_mm, coolant_flow_lpm: p.flow_lpm })}>
                          적용
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted">단위: mm · L/min · °C · K · kPa. 180° 굽힘 손실은 설계에 따라 냉각판 압력강하의 약 7–56 % 를 차지합니다.</p>
          </Card>

          <div className="grid gap-4 xl:grid-cols-2">
            <Card title="추천이 압력손실 모델 오차에 흔들리는가">
              <table className="w-full text-left text-xs">
                <thead className="text-muted"><tr className="border-b border-line">
                  {['손실 모델', '운전 가능', '추천', '기준 추천 유지'].map((h) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}
                </tr></thead>
                <tbody className="text-ink-2">
                  {result.loss_sensitivity.map((s) => (
                    <tr key={s.case} className="border-b border-line/60">
                      <td className="px-2 py-1.5 text-ink">{s.case}</td>
                      <td className="px-2 py-1.5 font-mono">{s.n_feasible}</td>
                      <td className="px-2 py-1.5 font-mono">{s.recommend ? `${s.recommend.width_mm}·${s.recommend.depth_mm}·${s.recommend.flow_lpm}` : '—'}</td>
                      <td className="px-2 py-1.5">{s.base_rec_still_feasible && s.base_rec_still_pareto ? '✓ 운전 가능·파레토' : s.base_rec_still_feasible ? '운전 가능 (파레토 아님)' : '✕ 운전 불가'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="mt-2 text-[11px] text-muted">마찰·굽힘·입출구 손실계수를 문헌 불확실성 범위로 바꿔 다시 계산했습니다. 열 결과는 유량만의 함수라 그대로이고 펌프 쪽만 달라집니다.</p>
            </Card>

            <Card title="CFD 검증 계획 (축약 모델 결과 확인)">
              <ul className="space-y-1 text-xs text-ink-2">
                {result.cfd_plan.candidates.map((c) => (
                  <li key={c.role}><b className="text-ink">{c.role}</b>: {design(c)} — {c.peak_core_c.toFixed(1)} °C · ΔT {c.peak_delta_t_k.toFixed(1)} K · {c.pump_w.toFixed(1)} W</li>
                ))}
              </ul>
              <p className="mt-2 text-xs font-semibold text-ink">비교 항목</p>
              <ul className="list-disc pl-5 text-xs text-ink-2">{result.cfd_plan.compare.map((x) => <li key={x}>{x}</li>)}</ul>
              <p className="mt-2 text-xs text-ink-2"><b className="text-ink">설정:</b> {result.cfd_plan.setup}</p>
              <p className="mt-1 text-xs text-ink-2"><b className="text-ink">판정:</b> {result.cfd_plan.accept}</p>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
