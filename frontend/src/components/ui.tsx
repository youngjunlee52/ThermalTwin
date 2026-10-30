import { useId, useState, type ReactNode } from 'react';
import type { RiskStatus } from '../types';
import { GLOSSARY } from '../lib/glossary';

/** 용어 설명: 글자 옆 ⓘ 에 마우스를 올리거나 눌러서(키보드 포커스 포함) 쉬운 설명을 봄 */
export function Term({ k, children }: { k: keyof typeof GLOSSARY | string; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  const text = GLOSSARY[k as string];
  if (!text) return <>{children ?? k}</>;
  return (
    <span className="relative inline-flex items-baseline gap-0.5">
      {children ?? k}
      <button type="button" aria-label={`${k} 뜻 보기`} aria-expanded={open}
        onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} onClick={() => setOpen((o) => !o)}
        className="ml-0.5 inline-grid size-3.5 place-items-center rounded-full bg-panel-2 text-[9px] font-bold leading-none text-muted ring-1 ring-line hover:text-ink">
        i
      </button>
      {open && (
        <span role="tooltip" className="absolute top-full left-0 z-40 mt-1 w-64 rounded-lg border border-axis bg-panel px-3 py-2 text-[11px] font-normal leading-relaxed whitespace-normal text-ink-2 normal-case shadow-lg">
          <b className="text-ink">{k}</b> — {text}
        </span>
      )}
    </span>
  );
}

export function Card({ title, right, children, className = '' }: {
  title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-xl border border-line bg-panel p-4 ${className}`}>
      {(title || right) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h3 className="text-sm font-semibold text-ink">{title}</h3>}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">{children}</h3>;
}

export function Slider({ label, unit, value, min, max, step, hint, onChange, format }: {
  label: string; unit: string; value: number; min: number; max: number; step: number;
  hint?: string; onChange: (v: number) => void; format?: (v: number) => string;
}) {
  const id = useId();
  const text = format ? format(value) : value.toFixed(step < 1 ? 1 : 0);
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-xs font-medium text-ink-2">{label}</label>
        <span className="font-mono text-sm tabular-nums text-ink">
          {text} <span className="text-muted">{unit}</span>
        </span>
      </div>
      <input id={id} type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))} className="w-full cursor-pointer" />
      {hint && <p className="text-[11px] leading-relaxed text-muted">{hint}</p>}
    </div>
  );
}

export function Segmented<T extends string | number>({ options, value, onChange, label }: {
  options: { value: T; label: string }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1 rounded-lg bg-panel-2 p-1">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button key={String(o.value)} type="button" role="radio" aria-checked={active}
            onClick={() => onChange(o.value)}
            className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium whitespace-nowrap transition-colors ${
              active ? 'bg-accent text-white' : 'text-ink-2 hover:bg-line'}`}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-3 text-xs text-ink-2">
      <span>{label}</span>
      <button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
        className={`relative h-5 w-9 rounded-full transition-colors ${checked ? 'bg-accent' : 'bg-axis'}`}>
        <span className={`absolute top-0.5 size-4 rounded-full bg-white transition-all ${checked ? 'left-[18px]' : 'left-0.5'}`} />
      </button>
    </label>
  );
}

// 상태 표시: 색 + 아이콘 + 글자 (색만으로 의미를 전달하지 않음)
export const RISK_META: Record<RiskStatus, { label: string; icon: string; color: string }> = {
  Safe: { label: '정상', icon: '●', color: 'var(--color-good)' },
  Warning: { label: '주의', icon: '▲', color: 'var(--color-warning)' },
  Danger: { label: '위험', icon: '■', color: 'var(--color-critical)' },
};

export function RiskBadge({ status }: { status: RiskStatus }) {
  const m = RISK_META[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-semibold ring-1 ${
      status === 'Danger' ? 'animate-danger-blink' : ''}`}
      style={{ color: m.color, borderColor: m.color, boxShadow: `inset 0 0 0 1px ${m.color}` }}>
      <span aria-hidden>{m.icon}</span>{m.label}
    </span>
  );
}
