import type { ModelsReport, SohCase } from '../../types';
import { Card } from '../ui';

const fmt = (v: number | null) => (v === null ? '보류' : v.toFixed(1));

function Row({ c, label }: { c: SohCase; label: string }) {
  const errNew = c.new === null ? null : c.new - c.true;
  const errOld = c.old - c.true;
  return (
    <tr className="border-b border-line/60">
      <td className="px-2 py-1.5 text-ink">{label}</td>
      <td className="px-2 py-1.5 text-right font-mono">{c.true.toFixed(0)}</td>
      <td className="px-2 py-1.5 text-right font-mono text-ink">{fmt(c.new)}</td>
      <td className="px-2 py-1.5 text-right font-mono">{c.lo !== null && c.hi !== null ? `${c.lo.toFixed(1)}–${c.hi.toFixed(1)}` : '—'}</td>
      <td className="px-2 py-1.5 text-right font-mono" style={{ color: errNew !== null && Math.abs(errNew) <= 1 ? 'var(--color-good)' : undefined }}>
        {errNew === null ? '—' : `${errNew >= 0 ? '+' : ''}${errNew.toFixed(1)}`}
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-muted">{c.old.toFixed(1)} ({errOld >= 0 ? '+' : ''}{errOld.toFixed(1)})</td>
    </tr>
  );
}

/** 리뷰 대응: 같은 SOH 에서 외부 조건만 바꿨을 때 SOH 추정이 흔들리는지 */
export default function SohSensitivity({ models }: { models: ModelsReport | null }) {
  const s = models?.soh_sensitivity;
  if (!s) {
    return (
      <Card title="SOH 추정 민감도 분석">
        <p className="text-sm text-muted">결과가 없습니다. <code className="font-mono">backend\.venv\Scripts\python.exe backend\soh_sensitivity.py</code> 를 실행하세요 (약 3분).</p>
      </Card>
    );
  }
  const { summary: sum } = s;
  return (
    <Card title="SOH 추정 민감도 분석 — 노화와 외부 환경을 구분하는가">
      <div className="mb-3 grid gap-2 sm:grid-cols-3">
        <div className="rounded-lg bg-panel-2 px-3 py-2">
          <p className="text-[11px] text-muted">실제 SOH 85 %, 외기·유량 9가지 조합: 최대 오차</p>
          <p className="font-mono text-lg text-ink">{sum.new.max_abs_err?.toFixed(1)} %p <span className="text-xs text-muted">(이전 방식 {sum.old.max_abs_err?.toFixed(1)} %p)</span></p>
        </div>
        <div className="rounded-lg bg-panel-2 px-3 py-2">
          <p className="text-[11px] text-muted">조건에 따른 흔들림 (최대 − 최소)</p>
          <p className="font-mono text-lg text-ink">{sum.new.spread?.toFixed(1)} %p <span className="text-xs text-muted">(이전 {sum.old.spread?.toFixed(1)} %p)</span></p>
        </div>
        <div className="rounded-lg bg-panel-2 px-3 py-2">
          <p className="text-[11px] text-muted">실제값이 95 % 구간 안에 든 경우</p>
          <p className="font-mono text-lg text-ink">{sum.coverage}</p>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-xs">
          <thead className="text-muted"><tr className="border-b border-line">
            {['조건', '실제 %', '추정 %', '95 % 구간', '오차 %p', '이전 방식 (오차)'].map((h, i) => (
              <th key={h} className={`px-2 py-2 font-medium ${i ? 'text-right' : ''}`}>{h}</th>
            ))}
          </tr></thead>
          <tbody className="text-ink-2">
            {s.grid_soh85.map((c) => <Row key={`g${c.ambient_c}-${c.flow_lpm}`} c={c} label={`외기 ${c.ambient_c} °C · 유량 ${c.flow_lpm} L/min`} />)}
            {s.levels.map((c) => <Row key={`l${c.true}`} c={c} label={`SOH 단계 · 25 °C · 10 L/min`} />)}
            {s.harsh.map((c) => <Row key={`h${c.true}`} c={c} label={`가혹 · ${c.power_kw} kW · ${c.ambient_c} °C · ${c.flow_lpm} L/min`} />)}
          </tbody>
        </table>
      </div>
      <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-muted">
        <p><b className="text-ink-2">원인과 수정:</b> 이전에는 노화 저항을 25 °C 기준 저항으로 역산해, 뜨거울수록(기본 저항이 작을수록) 노화를 과대평가했습니다. 지금은 노화 저항 = 측정 저항 − <i>현재 추정 코어온도·SOC 의</i> 기본 저항으로 구해 온도 영향을 분리합니다.</p>
        <p><b className="text-ink-2">혼동 방지:</b> 전류가 충분하고, 코어 10–45 °C, SOC 15–90 %, 코어 추정이 일치(모델 차이 &lt; 2 K)할 때만 천천히 갱신합니다. 조건을 벗어나면 틀린 값을 내는 대신 '보류'합니다 (가혹 조건 행). 신뢰구간에는 코어온도 불확실성이 기본 저항을 통해 들어오는 오차를 포함합니다.</p>
        <p><b className="text-ink-2">한계:</b> 시뮬레이션 차량은 분극(RC) 전압이 없고 트윈과 같은 저항 구조를 써서 결과가 낙관적입니다. 실차에서는 OCV 표와 분극 보정이 필요합니다. {s.method} · {s.generated_at}</p>
      </div>
    </Card>
  );
}
