import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { EvalSet, Frame, ModelsReport } from '../../types';
import { CHROME, SERIES } from '../../lib/colors';
import { Card } from '../ui';
import { axisProps, tooltipProps } from '../monitor/TrendCharts';
import SohSensitivity from './SohSensitivity';

const ROWS: { key: keyof EvalSet; label: string; note: string }[] = [
  { key: 'surface_as_core_rmse', label: '표면온도를 코어로 간주', note: '센서값을 그대로 쓰는 경우' },
  { key: 'datasheet_physics_rmse', label: '물리 모델 (데이터시트 R_in·C)', note: '기존 방식' },
  { key: 'data_only_rmse', label: '신경망 (데이터만)', note: '물리 손실 없음' },
  { key: 'pinn_rmse', label: 'PINN 신경망', note: '데이터 + 열전달 식 잔차' },
  { key: 'learned_physics_rmse', label: 'PINN 하이브리드 (학습한 R_in·C 로 물리 모델)', note: '실시간 대표 추정값' },
];

export default function PinnView({ models, latest }: { models: ModelsReport | null; latest: Frame | null }) {
  const p = models?.pinn;
  if (!p) return <Card title="PINN 모델"><p className="text-sm text-muted">학습 결과가 없습니다. backend 폴더에서 python build_models.py pinn 을 실행하세요.</p></Card>;
  const hist = p.history.map((h) => ({ it: h.it, data: h.data, phys: h.phys, R: h.R_in, C: h.C }));
  return (
    <div className="space-y-4">
      <Card title="PINN 코어온도 가상센서 — 무엇을, 어떻게 학습했나">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-2 text-sm leading-relaxed text-ink-2">
            <p>셀 코어온도는 실제 차량에서 측정할 수 없습니다. PINN 은 <b className="text-ink">측정 가능한 값(셀 표면온도·셀 발열 이력)</b>으로 코어온도를 추정하도록 학습됩니다.</p>
            <p className="rounded-lg bg-panel-2 p-3 font-mono text-xs text-ink">손실 = 데이터 손실 (코어 열전대가 있는 실험실 셀) + 물리 손실 C·dTc/dt − [q − (Tc − Ts)/R_in] = 0</p>
            <p>열용량 C 와 코어→표면 열저항 R_in 도 학습 변수입니다. 데이터시트 값이 실제와 달라도 데이터로 실제 값을 역추정합니다 (BINN 논문과 같은 구조).</p>
            <p className="text-xs text-muted">구조 {p.arch.layers.join(' → ')} (tanh) · 특징: {p.arch.features.join(', ')}</p>
            <p className="text-xs text-muted">학습: 가상 시나리오 {p.train.scenarios}개 중 {p.train.labeled_scenarios}개만 코어 라벨 사용 · 샘플 {p.train.pairs.toLocaleString()}쌍 · {p.train.iters}회 · {p.train.seconds}초 · {p.trained_at}</p>
          </div>
          <div>
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-muted"><tr className="border-b border-line">
                <th className="px-2 py-2 font-medium">파라미터</th><th className="px-2 py-2 font-medium">데이터시트</th>
                <th className="px-2 py-2 font-medium">PINN 학습값</th><th className="px-2 py-2 font-medium">실제 (시뮬레이션)</th>
              </tr></thead>
              <tbody className="font-mono tabular-nums text-ink-2">
                <tr className="border-b border-line/60"><td className="px-2 py-2 font-sans">R_in (K/W)</td><td className="px-2 py-2">{p.params.R_in_datasheet.toFixed(4)}</td><td className="px-2 py-2 text-ink">{p.params.R_in_learned.toFixed(4)}</td><td className="px-2 py-2">{p.params.R_in_true.toFixed(4)}</td></tr>
                <tr><td className="px-2 py-2 font-sans">C_core (J/K)</td><td className="px-2 py-2">{p.params.C_datasheet.toFixed(0)}</td><td className="px-2 py-2 text-ink">{p.params.C_learned.toFixed(0)}</td><td className="px-2 py-2">{p.params.C_true.toFixed(0)}</td></tr>
              </tbody>
            </table>
            <p className="mt-2 text-xs text-muted">R_in 은 데이터시트보다 35 % 큰 실제 값을 거의 정확히 찾았고, C 는 약 4 % 오차입니다.</p>
          </div>
        </div>
      </Card>

      <Card title="코어온도 추정 정확도 비교 (RMSE, K)">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead className="text-xs text-muted"><tr className="border-b border-line">
              <th className="px-2 py-2 font-medium">방법</th><th className="px-2 py-2 font-medium">학습 범위 안 (새 시나리오 16개)</th>
              <th className="px-2 py-2 font-medium">범위 밖 (180–230 kW · 30–45 °C)</th>
            </tr></thead>
            <tbody className="tabular-nums text-ink-2">
              {ROWS.map((r) => (
                <tr key={r.key} className={`border-b border-line/60 ${r.key === 'learned_physics_rmse' ? 'bg-panel-2' : ''}`}>
                  <td className="px-2 py-2"><span className="text-ink">{r.label}</span><br /><span className="text-xs text-muted">{r.note}</span></td>
                  <td className="px-2 py-2 font-mono">{p.eval.test[r.key].toFixed(2)}</td>
                  <td className="px-2 py-2 font-mono">{p.eval.ood[r.key].toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted">
          범위 밖에서 PINN 신경망의 최고온도는 {p.eval.ood.peak_core_pinn.toFixed(1)} °C 로 실제 {p.eval.ood.peak_core_true.toFixed(1)} °C 보다 낮게 나옵니다.
          그래서 실시간 위험 판정은 하이브리드와 신경망 중 높은 값을 쓰고, 둘이 2 K 이상 다르면 경고합니다.
        </p>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="학습 곡선: 데이터 손실 (K²)">
          <div className="h-48"><ResponsiveContainer width="100%" height="100%">
            <LineChart data={hist} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={CHROME.grid} vertical={false} />
              <XAxis dataKey="it" type="number" {...axisProps} />
              <YAxis {...axisProps} width={50} scale="log" domain={['auto', 'auto']} tickFormatter={(v: number) => v.toFixed(3)} />
              <Tooltip {...tooltipProps} labelFormatter={(v: unknown) => `반복 ${v}`} />
              <Line dataKey="data" name="데이터 손실" stroke={SERIES.blue} dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer></div>
        </Card>
        <Card title="학습 중 R_in 추정값 (K/W)">
          <div className="h-48"><ResponsiveContainer width="100%" height="100%">
            <LineChart data={hist} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={CHROME.grid} vertical={false} />
              <XAxis dataKey="it" type="number" {...axisProps} />
              <YAxis {...axisProps} width={50} domain={['auto', 'auto']} tickFormatter={(v: number) => v.toFixed(3)} />
              <Tooltip {...tooltipProps} labelFormatter={(v: unknown) => `반복 ${v}`} />
              <Line dataKey="R" name="R_in" stroke={SERIES.orange} dot={false} strokeWidth={2} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer></div>
          <p className="text-[11px] text-muted">데이터시트 0.120 에서 출발해 실제 값 {p.params.R_in_true.toFixed(3)} 쪽으로 이동</p>
        </Card>
      </div>

      <SohSensitivity models={models} />

      {latest && (
        <Card title="지금 실시간으로 쓰는 값">
          <p className="text-sm text-ink-2">
            현재 데이터: 하이브리드 코어 최고 <b className="text-ink">{latest.kpi.core_hybrid_max_c.toFixed(1)} °C</b> · PINN 신경망 <b className="text-ink">{latest.kpi.core_nn_max_c.toFixed(1)} °C</b> · 차이 {latest.kpi.model_gap_k.toFixed(2)} K
            {latest.twin.core_rmse_k !== undefined && <> · 실제 대비 RMSE 하이브리드 {latest.twin.core_rmse_k.toFixed(2)} K / 신경망 {latest.twin.core_nn_rmse_k?.toFixed(2)} K</>}
          </p>
          <p className="mt-1 text-xs text-muted">재학습: backend 폴더에서 <code className="font-mono">python build_models.py pinn</code> (약 1분). 실차 코어 데이터가 생기면 make_pairs 에 넣어 미세조정할 수 있습니다.</p>
        </Card>
      )}
    </div>
  );
}
