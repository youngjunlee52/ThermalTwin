import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { HistoryPoint } from '../../hooks/useSimulation';
import type { Frame } from '../../types';
import { CHROME, SERIES, STATUS } from '../../lib/colors';
import { T_CORE_DANGER_C, T_CORE_WARNING_C } from '../../types';
import { Card } from '../ui';

export const axisProps = { stroke: CHROME.axis, tick: { fill: CHROME.muted, fontSize: 11 }, tickLine: false } as const;
export const tooltipProps = {
  contentStyle: { background: CHROME.panel, border: `1px solid ${CHROME.axis}`, borderRadius: 8, fontSize: 12 },
  labelStyle: { color: CHROME.ink2 }, itemStyle: { color: CHROME.ink },
};
const line = { type: 'monotone' as const, dot: false, strokeWidth: 2, isAnimationActive: false, connectNulls: true };

function Chart({ data, children, unit, domain, xKey = 't_min', xFmt = (v: number) => `${v.toFixed(0)}m` }: {
  data: object[]; children: React.ReactNode; unit: string; domain?: unknown; xKey?: string; xFmt?: (v: number) => string;
}) {
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={CHROME.grid} vertical={false} />
          <XAxis dataKey={xKey} type="number" domain={['dataMin', 'dataMax']} {...axisProps} tickFormatter={xFmt} />
          <YAxis {...axisProps} width={44} unit={unit} domain={(domain as never) ?? ['auto', 'auto']} allowDecimals={false} />
          <Tooltip {...tooltipProps} labelFormatter={(v: unknown) => `t = ${Number(v).toFixed(1)} min`} />
          <Legend wrapperStyle={{ fontSize: 12, color: CHROME.ink2 }} iconType="plainline" />
          {children}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function TrendCharts({ history, frame }: { history: HistoryPoint[]; frame: Frame }) {
  const sim = frame.source === 'sim';
  const fc = frame.forecast.series.map((v, i) => ({ t: (i + 1) * 10 / 60, v }));
  const tempDomain = [(min: number) => Math.floor(Math.min(min, 20) - 2), (max: number) => Math.ceil(Math.max(max, T_CORE_DANGER_C) + 3)];
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Card title="최고 온도 추이 (°C)">
        <Chart data={history} unit="°" domain={tempDomain}>
          <ReferenceLine y={T_CORE_WARNING_C} stroke={STATUS.warning} strokeDasharray="4 4" label={{ value: '주의', fill: STATUS.warning, fontSize: 10, position: 'insideTopLeft' }} />
          <ReferenceLine y={T_CORE_DANGER_C} stroke={STATUS.critical} strokeDasharray="4 4" label={{ value: '위험', fill: STATUS.critical, fontSize: 10, position: 'insideTopLeft' }} />
          <Line {...line} dataKey="core" name="코어 (트윈)" stroke={SERIES.orange} />
          {sim ? <Line {...line} dataKey="true_core" name="코어 (실제·검증)" stroke={CHROME.ink2} strokeDasharray="5 4" />
            : <Line {...line} dataKey="core_nn" name="코어 (PINN 신경망)" stroke={SERIES.aqua} />}
          <Line {...line} dataKey="sensor_max" name="센서 최고 (표면)" stroke={SERIES.blue} />
        </Chart>
      </Card>
      <Card title={`앞으로 ${frame.forecast.horizon_min}분 예측: 최고 코어 (°C)`}>
        <Chart data={fc} unit="°" xKey="t" xFmt={(v) => `+${v.toFixed(0)}m`} domain={tempDomain}>
          <ReferenceLine y={T_CORE_WARNING_C} stroke={STATUS.warning} strokeDasharray="4 4" />
          <Line {...line} dataKey="v" name="예측 (PINN 보정 물리 모델)" stroke={SERIES.orange} legendType="none" />
        </Chart>
        <p className="mt-1 text-[11px] text-muted">제어 기준을 '10분 예측'으로 두면 이 값이 주의 온도에 닿기 전에 미리 출력을 줄입니다.</p>
      </Card>
      <Card title="출력 · 제어 명령 (kW)">
        <Chart data={history} unit="">
          <ReferenceLine y={0} stroke={CHROME.axis} />
          <Line {...line} dataKey="power" name="배터리 출력 (+충전)" stroke={SERIES.blue} />
          <Line {...line} dataKey="limit" name="충전 출력 상한 (명령)" stroke={SERIES.orange} strokeDasharray="6 3" />
        </Chart>
      </Card>
    </div>
  );
}
