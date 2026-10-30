import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { Frame } from '../../types';
import type { HistoryPoint } from '../../hooks/useSimulation';
import { CHROME, SERIES } from '../../lib/colors';
import { Card } from '../ui';
import { axisProps, tooltipProps } from './TrendCharts';

function Metric({ label, value, unit, note }: { label: string; value: string; unit?: string; note?: string }) {
  return (
    <div className="rounded-lg bg-panel-2 px-3 py-2">
      <p className="text-[11px] text-muted">{label}</p>
      <p className="font-mono text-base text-ink">{value} {unit && <span className="text-xs text-muted">{unit}</span>}</p>
      {note && <p className="text-[10px] text-muted">{note}</p>}
    </div>
  );
}

const cellName = ([r, c]: [number, number]) => `M${r + 1}-C${c + 1}`;

export default function TwinPanel({ frame: f, history }: { frame: Frame; history: HistoryPoint[] }) {
  const t = f.twin;
  const sim = f.source === 'sim';
  const rp = f.replay;
  return (
    <Card title="디지털 트윈 상태 · 검증">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Metric label="추정 SOH (셀 전압 · 온도 보정)" value={t.soh_est !== null ? t.soh_est.toFixed(1) : '추정 중'} unit={t.soh_est !== null ? '%' : undefined}
          note={`${t.soh_lo !== null && t.soh_hi !== null ? `95 % 구간 ${t.soh_lo.toFixed(1)}–${t.soh_hi.toFixed(1)} · ` : ''}${sim ? `실제 ${t.soh_true?.toFixed(0)} %` : '실차: 참값 없음'}`} />
        <Metric label="PINN 학습 R_in · C" value={`${t.R_in.toFixed(3)} · ${t.C_core.toFixed(0)}`} note="K/W · J/K (데이터시트 0.120 · 2291)" />
        <Metric label="신경망 ↔ 하이브리드 차이" value={f.kpi.model_gap_k.toFixed(2)} unit="K" note="2 K 넘으면 모델 점검 경고" />
        {sim ? (
          <>
            <Metric label="코어 추정 RMSE (하이브리드)" value={t.core_rmse_k?.toFixed(2) ?? '--'} unit="K" note="96셀 전체, 실제값 대비" />
            <Metric label="코어 추정 RMSE (신경망)" value={t.core_nn_rmse_k?.toFixed(2) ?? '--'} unit="K" />
            <Metric label="코어 최대 오차" value={t.core_max_err_k?.toFixed(2) ?? '--'} unit="K" />
          </>
        ) : rp ? (
          <>
            <Metric label="숨긴 프로브 예측 오차" value={rp.holdout_rmse_k?.toFixed(2) ?? '--'} unit="K" note={`트윈에 안 준 프로브 ${rp.holdout_probes.length}개 기준`} />
            <Metric label="비교: 평균값 대입" value={rp.mean_baseline_rmse_k?.toFixed(2) ?? '--'} unit="K" note="쓰는 프로브 평균으로 채웠을 때" />
            <Metric label="재생 진행" value={`${rp.row}/${rp.rows}`} note="10초 샘플" />
          </>
        ) : (
          <Metric label="수신 샘플" value={String(f.ingest.samples)} note={f.ingest.age_s !== null ? `마지막 수신 ${f.ingest.age_s}s 전` : '수신 대기'} />
        )}
      </div>
      <p className="mt-3 text-xs text-ink-2">
        약한 셀 후보 (셀 저항 상위): <b className="text-ink">{t.weak_cells.map(cellName).join(', ')}</b>
        {sim && t.true_weak_cells && <> · 실제 약한 셀: {t.true_weak_cells.map(cellName).join(', ')}</>}
      </p>
      <div className="mt-3 h-40">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={history} margin={{ top: 6, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={CHROME.grid} vertical={false} />
            <XAxis dataKey="t_min" type="number" domain={['dataMin', 'dataMax']} {...axisProps} tickFormatter={(v: number) => `${v.toFixed(0)}m`} />
            <YAxis domain={[60, 100]} {...axisProps} width={44} unit="%" />
            <Tooltip {...tooltipProps} labelFormatter={(v: unknown) => `t = ${Number(v).toFixed(1)} min`} />
            {sim && t.soh_true && <ReferenceLine y={t.soh_true} stroke={CHROME.ink2} strokeDasharray="5 4"
              label={{ value: `실제 ${t.soh_true.toFixed(0)} %`, fill: CHROME.ink2, fontSize: 10, position: 'insideBottomRight' }} />}
            <Area type="monotone" dataKey="soh_band" name="95 % 구간" stroke="none" fill={SERIES.blue} fillOpacity={0.18} isAnimationActive={false} connectNulls={false} />
            <Line type="monotone" dataKey="soh_est" name="추정 SOH" stroke={SERIES.blue} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[11px] text-muted">
        {t.soh_status} · 갱신 {t.soh_updates}회 / 보류 {t.soh_skipped}회 (이전 방식 {t.soh_raw.toFixed(1)} %).
        노화 저항 = 측정 저항 − 현재 온도·SOC 의 기본 저항 → BINN 노화식을 거꾸로 풉니다. 코어 10–45 °C, SOC 15–90 %, 코어 추정 일치 시에만 천천히 갱신.
      </p>
    </Card>
  );
}
