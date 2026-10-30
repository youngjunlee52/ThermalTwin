import type { ModelsReport, RobustResult } from '../../types';
import { Card } from '../ui';

const k = (v: number | null | undefined) => (v === null || v === undefined ? '—' : v.toFixed(2));

function Under({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <>—</>;
  // + 면 실제보다 낮게 봄(위험 쪽), − 면 높게 봄(안전 쪽)
  const color = v > 1 ? 'var(--color-critical)' : v > 0.3 ? 'var(--color-warning)' : 'var(--color-good)';
  return <span style={{ color }}>{v > 0 ? `${v.toFixed(1)} K 낮게` : `${(-v).toFixed(1)} K 높게`}</span>;
}

function Cells({ r }: { r: RobustResult }) {
  return (
    <>
      <td className="px-2 py-1.5 text-right font-mono">{k(r.core_rmse_k)}</td>
      <td className="px-2 py-1.5 text-right font-mono"><Under v={r.fault_cell_max_underestimate_k ?? r.hottest_cell_underestimate_k} /></td>
    </>
  );
}

/** 리뷰 대응 3순위: 국부 과열·센서 고장에서 트윈이 버티는가 */
export default function RobustnessCard({ models }: { models: ModelsReport | null }) {
  const rb = models?.robustness;
  if (!rb) {
    return (
      <Card title="고장 상황 강건성 검증">
        <p className="text-sm text-muted">결과가 없습니다. <code className="font-mono">backend\.venv\Scripts\python.exe backend\robustness.py</code> 를 실행하세요 (약 1분).</p>
      </Card>
    );
  }
  return (
    <Card title="고장 상황 강건성 검증 — 국부 과열·센서 고장">
      <p className="mb-2 text-xs text-muted">{rb.conditions}. '최악 셀'은 고장 셀(냉각 불량) 또는 팩 최고 셀을 실제보다 얼마나 낮게/높게 봤는지 (낮게 = 위험 쪽).</p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-xs">
          <thead className="text-muted">
            <tr className="border-b border-line">
              <th className="px-2 py-2 font-medium" rowSpan={2}>상황</th>
              <th className="px-2 py-1 text-center font-medium" colSpan={2}>센서 고장 진단 없음</th>
              <th className="px-2 py-1 text-center font-medium" colSpan={2}>진단 사용 (v0.4)</th>
              <th className="px-2 py-2 text-right font-medium" rowSpan={2}>감지</th>
            </tr>
            <tr className="border-b border-line">
              {['코어 RMSE K', '최악 셀', '코어 RMSE K', '최악 셀'].map((h, i) => <th key={i} className="px-2 py-1 text-right font-medium">{h}</th>)}
            </tr>
          </thead>
          <tbody className="text-ink-2">
            {rb.rows.map((row) => (
              <tr key={row.name} className="border-b border-line/60">
                <td className="px-2 py-1.5 text-ink">{row.name}</td>
                <Cells r={row.before} />
                <Cells r={row.after} />
                <td className="px-2 py-1.5 text-right font-mono">
                  {row.after.sensor_flagged_after_s !== null ? `${Math.max(0, row.after.sensor_flagged_after_s)} s` : '—'}
                  {row.after.false_sensor_flags > 0 && <span style={{ color: 'var(--color-critical)' }}> · 오경보 {row.after.false_sensor_flags}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h4 className="mt-4 mb-1 text-xs font-semibold text-ink">셀 하나의 냉각 불량(냉각 10 %)을 얼마나 놓치는가 — 센서 수·거리별</h4>
      <div className="flex flex-wrap gap-2">
        {rb.cooling_coverage.map((c) => (
          <div key={`${c.n_sensors}-${c.where}`} className="rounded-lg bg-panel-2 px-3 py-2 text-xs">
            <p className="text-muted">센서 {c.n_sensors}개 · {c.where}</p>
            <p className="font-mono text-ink"><Under v={c.fault_cell_max_underestimate_k} /></p>
          </div>
        ))}
      </div>
      <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-muted">
        <p><b className="text-ink-2">발견:</b> {rb.finding}</p>
        <p><b className="text-ink-2">센서 고장 진단 규칙:</b> 각 센서를 나머지 센서로 예측해 혼자 튀는지 봅니다. 주변보다 <i>차갑게</i> 읽거나 값이 멈추면 열을 숨길 수 있어 격리(✕). 주변보다 <i>뜨겁게</i> 읽으면 국부 과열일 수 있어 버리지 않고 그 셀에만 반영하며 현장 확인을 요청(!). 처음에는 뜨거운 센서도 격리했다가, 냉각 불량 셀의 정상 센서를 숨겨 과열을 9 K 놓치는 문제를 발견해 이렇게 바꿨습니다.</p>
        <p><b className="text-ink-2">시도했지만 채택하지 않은 방법:</b> {rb.tried_not_adopted.join(' / ')}</p>
        <p>생성 {rb.generated_at}</p>
      </div>
    </Card>
  );
}
