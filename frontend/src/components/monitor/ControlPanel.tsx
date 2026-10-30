import type { Frame } from '../../types';
import { CONTROL_MODE_META } from '../../types';
import { Card, Term } from '../ui';

const BASIS_LABEL: Record<string, string> = {
  twin: '계산한 셀 내부 온도', sensor: '겉면 센서 최고 온도', forecast: '10분 뒤 예상 온도',
  rule: '단순 규칙: 센서 최고 + 5 °C 와 계산값 중 높은 쪽',
};

function HealthRow({ label, value, limit, unit, digits = 2 }: { label: string; value: number | null | undefined; limit: number; unit: string; digits?: number }) {
  const v = value ?? 0;
  const ratio = Math.min(1, v / limit);
  const color = ratio >= 1 ? 'var(--color-critical)' : ratio >= 0.7 ? 'var(--color-warning)' : 'var(--color-good)';
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-[11px]">
        <span className="text-ink-2">{label}</span>
        <span className="font-mono text-ink">{value === null || value === undefined ? '--' : v.toFixed(digits)} <span className="text-muted">/ {limit} {unit}</span></span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-panel-2" role="meter" aria-label={label} aria-valuenow={v} aria-valuemax={limit}>
        <div className="h-full rounded-full" style={{ width: `${ratio * 100}%`, background: color }} />
      </div>
    </div>
  );
}

/** 운용 화면의 핵심: 지금 누가(트윈/규칙) 어떤 명령을 냈고, 그 명령이 유효하며 어디에 적용되는지 */
export default function ControlPanel({ frame: f, onForceFallback }: { frame: Frame; onForceFallback?: (v: boolean) => void }) {
  const ctl = f.control;
  const cmd = f.commands;
  const meta = CONTROL_MODE_META[ctl.mode];
  const fb = ctl.fallback;
  const h = fb.health;
  const lim = h.limits;
  const border = !ctl.valid ? 'border-critical' : ctl.mode === 'fallback' ? 'border-serious' : 'border-line';

  return (
    <Card className={border} title={<>자동 보호 상태 (이 프로그램 → 차량 <Term k="BMS" />)</>}
      right={onForceFallback &&
        <button type="button" aria-pressed={ctl.fallback.forced} onClick={() => onForceFallback(!ctl.fallback.forced)}
          className={`rounded-md px-3 py-1.5 text-xs font-semibold ring-1 ${ctl.fallback.forced
            ? 'bg-serious text-white ring-serious' : 'bg-panel-2 text-ink ring-line hover:bg-line'}`}>
          {ctl.fallback.forced ? '◆ 안전 모드 고정 중 — 풀기' : '◆ 안전 모드로 바꾸기'}
        </button>
      }>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-2">
          <p className="flex items-center gap-2 text-lg font-semibold" style={{ color: ctl.valid ? meta.color : 'var(--color-critical)' }}>
            <span aria-hidden>{ctl.valid ? meta.icon : '✕'}</span>
            {ctl.valid ? meta.label : '명령 무효 (데이터 끊김)'}
          </p>
          <p className="text-xs text-ink-2">무엇을 보고 판단: <b className="text-ink">{ctl.mode === 'off' ? '없음' : BASIS_LABEL[ctl.basis ?? 'twin'] ?? ctl.basis}</b></p>
          <p className="text-xs text-ink-2">어디에 적용: <b className="text-ink">{ctl.applied}</b></p>
          {!ctl.valid && (
            <p className="text-xs" style={{ color: 'var(--color-critical)' }}>
              외부 데이터가 {ctl.age_s ?? '--'} s 동안 없습니다 ({ctl.stale_after_s} s 초과). 마지막 명령은 쓰지 말고 BMS 자체 규칙으로 동작해야 합니다.
            </p>
          )}
        </div>

        <div className="space-y-1.5 rounded-lg bg-panel-2 p-3 font-mono text-xs leading-relaxed text-ink-2">
          <p className="font-sans text-[11px] text-muted">차량에 보내는 권고 {cmd.issued_at !== undefined ? `· t = ${(cmd.issued_at / 60).toFixed(1)} 분` : ''}</p>
          <p>충전 세기 최대 <b className="text-ink">{cmd.charge_limit_kw.toFixed(0)} kW</b> ({(cmd.derate * 100).toFixed(0)} %)</p>
          <p>냉각수 <b className="text-ink">{cmd.coolant_flow_lpm.toFixed(1)} L/min</b> · 목표 {cmd.coolant_set_c} °C</p>
          <p className="font-sans text-ink-2">{cmd.reason}</p>
        </div>

        <div className="space-y-2">
          <p className="text-[11px] text-muted">AI 계산을 믿어도 되는지 점검 — 막대가 끝까지 차는 상태가 3번 이어지면 <Term k="안전모드">안전 모드</Term>로 바뀝니다</p>
          {lim && (
            <>
              <HealthRow label="두 계산 방식의 차이" value={h.model_gap_k} limit={lim.model_gap_k} unit="K" />
              <HealthRow label="계산의 불확실성" value={h.mean_std_k} limit={lim.mean_std_k} unit="K" digits={3} />
              <HealthRow label="센서 값과 계산의 차이" value={h.residual_rmse_k} limit={lim.residual_rmse_k} unit="K" />
              {f.source === 'ingest' && <HealthRow label="연속으로 거부된 입력" value={h.consec_rejects} limit={lim.consec_rejects} unit="회" digits={0} />}
            </>
          )}
        </div>
      </div>

      {fb.active && (
        <div role="alert" className="mt-3 rounded-lg border border-serious px-3 py-2 text-xs text-ink-2">
          <b style={{ color: 'var(--color-serious)' }}>◆ 안전 모드 전환 이유</b>
          {fb.since_t !== null && <span className="text-muted"> · t = {(fb.since_t / 60).toFixed(1)} 분부터</span>}
          <ul className="mt-1 list-disc pl-5">{fb.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          <p className="mt-1 text-muted">AI 추정 대신 '센서 최고 + 5 K'와 트윈 추정 중 높은 값으로 출력을 제한하고 냉각수 유량을 20 L/min 이상으로 올립니다. 화면의 디레이팅 설정과 상관없이 적용되며, 모든 점검 항목이 한계의 70 % 아래로 60초(차량 시각) 유지되면 AI 제어로 돌아갑니다.</p>
        </div>
      )}
    </Card>
  );
}
