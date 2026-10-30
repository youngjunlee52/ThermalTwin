import { useState } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Cell, Frame, ModelsReport } from '../../types';
import { CHROME, SERIES } from '../../lib/colors';
import { Card, Segmented } from '../ui';
import { axisProps, tooltipProps } from '../monitor/TrendCharts';
import RobustnessCard from './RobustnessCard';

const line = { type: 'monotone' as const, strokeWidth: 2, isAnimationActive: false, dot: { r: 4 } };

function Mini({ cells }: { cells: Cell[] }) {
  const set = new Set(cells.map(([r, c]) => `${r}-${c}`));
  return (
    <div className="grid w-full max-w-[240px] gap-[2px]" style={{ gridTemplateColumns: 'repeat(12, minmax(0,1fr))' }}>
      {Array.from({ length: 96 }, (_, i) => {
        const r = Math.floor(i / 12);
        const c = i % 12;
        return <div key={i} className="aspect-square rounded-[2px]" style={{ background: set.has(`${r}-${c}`) ? '#3987e5' : '#2c2c2a' }} />;
      })}
    </div>
  );
}

export default function SensorView({ models, latest, onSensors }: { models: ModelsReport | null; latest: Frame | null; onSensors: (c: Cell[]) => void }) {
  const [n, setN] = useState(8);
  const pl = models?.placement;
  if (!pl) return <Card title="센서 배치 분석"><p className="text-sm text-muted">모델 결과를 불러오는 중… (backend/models 폴더가 필요합니다)</p></Card>;

  const analytic = pl.counts.map((c, i) => ({ n: c, greedy: pl.greedy[i], uniform: pl.uniform[i], random: pl.random[i] }));
  const sv = pl.sim_validation;
  const simRows = sv ? sv.counts.map((c, i) => ({ n: c, v_greedy: sv.with_voltage.greedy[i].core_rmse, v_uniform: sv.with_voltage.uniform[i].core_rmse, t_uniform: sv.temp_only.uniform[i].core_rmse })) : [];
  const real = models?.real_validation;
  const sim = latest?.source === 'sim';

  return (
    <div className="space-y-4">
      <Card title="센서 수·위치가 바뀌면 추정 오차가 달라지는 문제 — 이렇게 줄였습니다">
        <ol className="list-decimal space-y-1.5 pl-5 text-sm text-ink-2">
          <li><b className="text-ink">셀 전압 96개 활용</b>: 실제 BMS 는 온도센서는 일부만 있지만 셀 전압은 모두 잽니다. 전압으로 셀마다 저항(=발열)을 식별하므로, 센서가 없는 셀의 발열도 압니다.</li>
          <li><b className="text-ink">학습된 공간 공분산(GP) 보정</b>: 센서 조합이 바뀔 때마다 최적 보정 가중치를 다시 계산합니다. 정해진 위치에 의존하는 보간식이 아닙니다.</li>
          <li><b className="text-ink">불확실성 지도</b>: 셀마다 "얼마나 모르는지(σ)"를 표시합니다 (실시간 모니터 → 불확실성).</li>
          <li><b className="text-ink">최적 배치 계산</b>: 같은 공분산으로 오차를 가장 많이 줄이는 위치를 골라 줍니다 (탐욕 알고리즘).</li>
        </ol>
        <p className="mt-2 text-xs text-muted">센서를 없애도 오차가 0 이 되지는 않습니다. 다만 아래 결과처럼 셀 전압을 쓰면 센서 수에 거의 영향을 받지 않습니다.</p>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="시뮬레이션 폐루프 검증: 센서 수별 코어온도 RMSE (K)">
          {sv ? (
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={simRows} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={CHROME.grid} vertical={false} />
                  <XAxis dataKey="n" type="number" scale="log" domain={[3, 40]} ticks={[4, 8, 16, 32]} {...axisProps} />
                  <YAxis {...axisProps} width={40} domain={[0, 'auto']} />
                  <Tooltip {...tooltipProps} labelFormatter={(v: unknown) => `센서 ${v}개`} formatter={(v: unknown) => `${Number(v).toFixed(2)} K`} />
                  <Legend wrapperStyle={{ fontSize: 12, color: CHROME.ink2 }} />
                  <Line {...line} dataKey="t_uniform" name="온도센서만 (균등 배치)" stroke={SERIES.orange} />
                  <Line {...line} dataKey="v_uniform" name="+ 셀 전압 (균등 배치)" stroke={SERIES.blue} />
                  <Line {...line} dataKey="v_greedy" name="+ 셀 전압 (최적 배치)" stroke={SERIES.aqua} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : <p className="text-sm text-muted">검증 결과 없음 (python build_models.py validate)</p>}
          <p className="mt-1 text-xs text-muted">시나리오 3개(급속충전 180 kW·30 °C, 120 kW·10 °C, 가혹주행·35 °C) 평균. 약한 셀·유량 편차·데이터시트 오차가 숨어 있는 가상 차량 기준.</p>
        </Card>

        <Card title="실차 검증: 프로브 일부만 쓰고 나머지 맞히기 (K)">
          {real ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[420px] text-left text-xs">
                <thead className="text-muted"><tr className="border-b border-line">
                  <th className="px-2 py-2 font-medium">구간</th><th className="px-2 py-2 font-medium">사용 프로브</th>
                  <th className="px-2 py-2 font-medium">트윈</th><th className="px-2 py-2 font-medium">평균 대입</th><th className="px-2 py-2 font-medium">가까운 프로브</th>
                </tr></thead>
                <tbody className="font-mono tabular-nums text-ink-2">
                  {Object.entries(real).flatMap(([name, d]) => d.by_n.map((r, i) => (
                    <tr key={`${name}-${i}`} className="border-b border-line/60">
                      <td className="px-2 py-1.5 font-sans">{i === 0 ? d.title : ''}</td>
                      <td className="px-2 py-1.5">{r.n_use} / 32</td>
                      <td className="px-2 py-1.5 text-ink">{r.rmse.toFixed(2)}</td>
                      <td className="px-2 py-1.5">{r.mean_baseline_rmse.toFixed(2)}</td>
                      <td className="px-2 py-1.5">{r.nearest_baseline_rmse?.toFixed(2) ?? '--'}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          ) : <p className="text-sm text-muted">실차 검증 결과 없음</p>}
          <p className="mt-2 text-xs text-muted">300대 EV 공개 데이터(1 °C 분해능 프로브 32개). 공간 공분산은 평가 구간을 뺀 다른 구간에서만 학습(교차검증). 프로브 실제 위치는 비공개라 모듈당 4개 균등 배치로 가정.</p>
        </Card>
      </div>

      <Card title="최적 센서 배치" right={<Segmented label="센서 수" value={n} onChange={setN} options={[4, 8, 12, 16, 24, 32].map((v) => ({ value: v, label: `${v}개` }))} />}>
        <div className="grid gap-6 md:grid-cols-[1fr_auto_auto]">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={analytic} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={CHROME.grid} vertical={false} />
                <XAxis dataKey="n" type="number" scale="log" domain={[2, 48]} ticks={[2, 4, 8, 16, 32, 48]} {...axisProps} />
                <YAxis {...axisProps} width={44} domain={[0, 'auto']} tickFormatter={(v: number) => v.toFixed(2)} />
                <Tooltip {...tooltipProps} labelFormatter={(v: unknown) => `센서 ${v}개`} formatter={(v: unknown) => `${Number(v).toFixed(3)} K`} />
                <Legend wrapperStyle={{ fontSize: 12, color: CHROME.ink2 }} />
                <Line {...line} dataKey="random" name="무작위" stroke={SERIES.orange} />
                <Line {...line} dataKey="uniform" name="균등" stroke={SERIES.blue} />
                <Line {...line} dataKey="greedy" name="최적 (탐욕)" stroke={SERIES.aqua} />
              </LineChart>
            </ResponsiveContainer>
            <p className="text-[11px] text-muted">GP 사후분산으로 계산한 표면온도 예상 오차 (셀 전압 식별 후 남는 오차, 센서 없을 때 {pl.prior_rmse.toFixed(2)} K)</p>
          </div>
          <div className="space-y-2">
            <p className="text-xs text-ink-2">균등 배치 {n}개</p>
            <Mini cells={pl.layouts.uniform[String(n)] ?? []} />
            <button type="button" disabled={!sim} onClick={() => onSensors(pl.layouts.uniform[String(n)] ?? [])}
              className="rounded-md bg-panel-2 px-3 py-1.5 text-xs text-ink ring-1 ring-line hover:bg-line disabled:opacity-40">적용</button>
          </div>
          <div className="space-y-2">
            <p className="text-xs text-ink-2">최적 배치 {n}개</p>
            <Mini cells={pl.layouts.greedy[String(n)] ?? []} />
            <button type="button" disabled={!sim} onClick={() => onSensors(pl.layouts.greedy[String(n)] ?? [])}
              className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40">적용</button>
          </div>
        </div>
        <p className="mt-3 text-xs text-muted">최적 배치는 냉각수 하류(출구 쪽)에 몰리는데, 그곳이 유량 편차에 따른 오차가 가장 큰 곳이기 때문입니다. '적용'은 시뮬레이션 소스에서만 동작합니다.</p>
      </Card>

      <RobustnessCard models={models} />
    </div>
  );
}
