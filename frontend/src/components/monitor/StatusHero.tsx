import { useState } from 'react';
import type { Frame } from '../../types';
import { T_CORE_DANGER_C, T_CORE_WARNING_C } from '../../types';
import { RISK_META, Term } from '../ui';

const SCALE_MIN = 10;
const SCALE_MAX = 90;
const pos = (t: number) => `${Math.min(100, Math.max(0, ((t - SCALE_MIN) / (SCALE_MAX - SCALE_MIN)) * 100))}%`;

/** 지금 시스템이 하는 일 (쉬운 말) */
function doing(f: Frame): string {
  const c = f.commands;
  if (!f.control.valid) return '차량 데이터가 끊겨 명령을 보내지 않고 있습니다';
  if (f.control.mode === 'fallback') return `AI 계산을 믿기 어려워 안전 규칙으로 보호 중 — 충전 출력 ${(c.derate * 100).toFixed(0)} %, 냉각수 ${c.coolant_flow_lpm.toFixed(0)} L/min`;
  if (c.derate < 0.999) return `배터리가 뜨거워 충전 출력을 ${(c.derate * 100).toFixed(0)} % 로 줄이는 중`;
  if (f.mode === 'fast_charge' && f.current_a < -5) return `정상 속도로 충전 중 (${Math.abs(f.power_kw).toFixed(0)} kW)`;
  if (f.mode === 'rest' || Math.abs(f.current_a) < 5) return '쉬는 중 (충전·주행 없음)';
  return `주행 중 (${Math.abs(f.power_kw).toFixed(0)} kW 사용)`;
}

/** 사용자가 할 일 (쉬운 말) */
function todo(f: Frame): string {
  const r = f.risk_reasons.join(' ');
  if (!f.control.valid) return '차량(게이트웨이) 연결을 확인하세요. 그동안 차량은 자체 보호 기능으로 움직입니다.';
  if (f.risk_status === 'Danger') return '충전을 멈추고 냉각 장치와 배터리를 점검하세요.';
  if (r.includes('온도센서')) return '표시된 온도센서를 점검하세요 (히트맵의 ✕ 또는 ! 표시).';
  if (f.risk_status === 'Warning') return '자동 보호가 작동 중입니다. 온도가 계속 오르면 충전 출력을 낮추세요.';
  return '할 일 없음 — 그대로 지켜보면 됩니다.';
}

/** 화면 맨 위: 배터리 상태를 한 문장으로 */
export default function StatusHero({ frame: f }: { frame: Frame }) {
  const [more, setMore] = useState(false);
  const m = RISK_META[f.risk_status];
  const hot = f.kpi.core_max_c;
  const [hr, hc] = f.kpi.hotspot;
  const word = m.label;
  const note = [f.status_note, !f.running ? (f.replay && f.replay.row >= f.replay.rows ? '재생이 끝났습니다' : '일시정지됨') : ''].filter(Boolean).join(' · ');

  return (
    <section aria-live="polite"
      className={`rounded-2xl border-2 bg-panel p-5 ${f.risk_status === 'Danger' ? 'animate-danger-blink' : ''}`}
      style={{ borderColor: m.color }}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1">
          <p className="text-xs text-muted">지금 배터리 상태</p>
          <p className="flex items-center gap-2 text-3xl font-bold" style={{ color: m.color }}>
            <span aria-hidden>{m.icon}</span>{word}
          </p>
          <p className="text-sm text-ink-2">
            가장 뜨거운 셀(모듈 {hr + 1}, {hc + 1}번째) <Term k="코어온도">내부 온도</Term> <b className="text-ink">{hot.toFixed(1)} °C</b>
            {' '}— 주의 {T_CORE_WARNING_C} °C, 위험 {T_CORE_DANGER_C} °C
          </p>
        </div>
        <div className="max-w-xl space-y-2 text-sm">
          <p><span className="text-muted">시스템이 하는 일 · </span><span className="text-ink">{doing(f)}</span></p>
          <p><span className="text-muted">내가 할 일 · </span><b className="text-ink">{todo(f)}</b></p>
          {note && <p className="text-xs text-muted">{note}</p>}
        </div>
      </div>

      {/* 온도 막대: 지금 위치와 기준선 */}
      <div className="mt-4" aria-hidden>
        <div className="relative h-3 rounded-full"
          style={{ background: `linear-gradient(90deg, var(--color-good) 0%, var(--color-good) ${pos(T_CORE_WARNING_C)}, var(--color-warning) ${pos(T_CORE_WARNING_C)}, var(--color-warning) ${pos(T_CORE_DANGER_C)}, var(--color-critical) ${pos(T_CORE_DANGER_C)}, var(--color-critical) 100%)`, opacity: 0.55 }}>
          <span className="absolute -top-1.5 h-6 w-1.5 -translate-x-1/2 rounded bg-ink shadow" style={{ left: pos(hot) }} />
        </div>
        <div className="relative mt-1 h-4 font-mono text-[10px] text-muted">
          <span className="absolute -translate-x-1/2" style={{ left: pos(SCALE_MIN + 0.01) }}>{SCALE_MIN} °C</span>
          <span className="absolute -translate-x-1/2" style={{ left: pos(T_CORE_WARNING_C) }}>주의 {T_CORE_WARNING_C}</span>
          <span className="absolute -translate-x-1/2" style={{ left: pos(T_CORE_DANGER_C) }}>위험 {T_CORE_DANGER_C}</span>
          <span className="absolute -translate-x-1/2" style={{ left: pos(f.runaway.onset_c) }}><Term k="열폭주">열폭주 시작 {f.runaway.onset_c.toFixed(0)}</Term></span>
        </div>
      </div>

      {f.risk_reasons.length > 0 && (
        <div className="mt-3">
          <button type="button" onClick={() => setMore(!more)} aria-expanded={more} className="text-xs text-accent hover:underline">
            {more ? '▾ 자세한 이유 접기' : `▸ 자세한 이유 보기 (${f.risk_reasons.length})`}
          </button>
          {more && <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-ink-2">{f.risk_reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
        </div>
      )}
    </section>
  );
}
