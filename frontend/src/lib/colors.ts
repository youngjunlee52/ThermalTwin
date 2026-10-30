// 데이터 색상 — 역할별로 고정 (dataviz 기준: 범주 색은 순서 고정, 연속값은 단일 색상 램프, 상태색은 예약)

// 범주(시리즈) 색 — 다크 배경용, 앞 3개 슬롯만 사용
export const SERIES = {
  blue: '#3987e5',
  orange: '#d95926',
  aqua: '#199e70',
} as const;

// 상태 색 — 항상 아이콘 + 글자와 함께 사용
export const STATUS = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
} as const;

export const CHROME = {
  grid: '#2c2c2a',
  axis: '#383835',
  muted: '#898781',
  ink: '#f0efec',
  ink2: '#c3c2b7',
  panel: '#1a1a19',
} as const;

// 온도용 단일 색상(주황) 연속 램프: 어두움(낮은 온도) → 밝음(높은 온도), OKLCH 로 계산
export const HEAT_RAMP = [
  '#4b1f08', '#6e2a00', '#8e3902', '#af490a', '#d35805', '#e8763d', '#f3986f', '#f6bda3', '#f1e1da',
];

// 오차용 발산 램프 (실제 − 트윈): 파랑(트윈이 높게 추정) ← 회색(0) → 빨강(실제가 더 뜨거움)
const DIVERGING = ['#3987e5', '#4f74a6', '#455468', '#383835', '#6e4a48', '#a85a59', '#e66767'];

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function interpolate(stops: string[], t: number): string {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  const f = x - i;
  const a = hexToRgb(stops[i]);
  const b = hexToRgb(stops[i + 1]);
  const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
  return `rgb(${c[0]} ${c[1]} ${c[2]})`;
}

export function heatColor(value: number, min: number, max: number): string {
  return interpolate(HEAT_RAMP, (value - min) / (max - min));
}

export function errorColor(value: number, span: number): string {
  return interpolate(DIVERGING, (value + span) / (2 * span));
}

export function heatGradientCss(): string {
  return `linear-gradient(to right, ${HEAT_RAMP.join(', ')})`;
}

export function errorGradientCss(): string {
  return `linear-gradient(to right, ${DIVERGING.join(', ')})`;
}

// 배경이 밝으면 어두운 글자
export function inkOn(value: number, min: number, max: number): string {
  return (value - min) / (max - min) > 0.62 ? '#1a1a19' : '#f0efec';
}

// 온도 이외의 크기(불확실성·저항 배율)용 단일 색상(파랑) 연속 램프: 어두움(작음) → 밝음(큼)
const BLUE_RAMP = ['#0d366b', '#184f95', '#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];

export function seqColor(value: number, min: number, max: number): string {
  return interpolate(BLUE_RAMP, (value - min) / (max - min || 1));
}

export function seqGradientCss(): string {
  return `linear-gradient(to right, ${BLUE_RAMP.join(', ')})`;
}
