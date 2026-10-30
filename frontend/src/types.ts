// 백엔드 engine.py 가 1초마다 보내는 데이터 형식 (v0.4)

export type RiskStatus = 'Safe' | 'Warning' | 'Danger';
export type Mode = 'fast_charge' | 'highway' | 'city' | 'aggressive' | 'rest';
export type Source = 'sim' | 'replay' | 'ingest';
export type ConnectionStatus = 'connecting' | 'open' | 'closed';
export type Grid = number[][]; // [8 모듈][12 셀]
export type Cell = [number, number];

export interface Controls {
  source: Source;
  dataset: string;
  mode: Mode;
  charge_power_kw: number;
  ambient_c: number;
  coolant_set_c: number;
  coolant_flow_lpm: number;
  channel_width_mm: number;
  channel_depth_mm: number;
  true_soh: number;
  derating: boolean;
  derate_source: 'sensor' | 'twin' | 'forecast';
  auto_pump: boolean;
  use_voltage: boolean;
  time_scale: number;
  force_fallback: boolean;
}

export type ControlMode = 'twin' | 'fallback' | 'off';

export interface Commands {
  charge_limit_kw: number;
  coolant_flow_lpm: number;
  coolant_set_c: number;
  derate: number;
  reason: string;
  t_control_c?: number;
  issued_at?: number;
  mode?: ControlMode;
  basis?: string;
  fallback_reasons?: string[];
}

/** 제어 상태: 지금 명령이 어떤 방식으로 만들어졌고, 유효한지, 어디에 적용되는지 */
export interface ControlStatus {
  mode: ControlMode;
  basis: string | null;
  valid: boolean;
  stale: boolean;
  age_s: number | null;
  stale_after_s: number;
  applied: string;
  closed_loop: boolean;
  fallback: {
    active: boolean;
    forced: boolean;
    reasons: string[];
    since_t: number | null;
    health: {
      model_gap_k?: number; mean_std_k?: number; residual_rmse_k?: number | null; n_sensors?: number; consec_rejects?: number;
      limits?: { model_gap_k: number; mean_std_k: number; residual_rmse_k: number; consec_rejects: number };
    };
  };
}

export interface InputPriority {
  source: Source;
  locked: (keyof Controls)[];
  user_hold: boolean;
  redirected_samples: number;
  rule: string;
}

/** 열폭주 반응열 모델 (SEI 분해 자기발열) — backend/runaway.py */
export interface Runaway {
  level: RiskStatus;
  adiabatic_hours: number;     // 냉각이 끊겼을 때 폭주까지 걸리는 시간 (단열 유도시간)
  onset_c: number;             // 자기발열 개시온도 (ARC 0.02 K/min)
  margin_to_onset_k: number;
  semenov_critical_c: number;  // 냉각이 살아 있어도 자기발열이 이기는 온도
  cooled_stable: boolean;
  self_heat_max_w: number;
  hottest: Cell;
}

export interface FleetVehicle {
  vehicle_id: string;
  source: Source;
  risk_status: RiskStatus | null;
  core_max_c: number | null;
  samples: number;
  rejected: number;
  age_s: number | null;
  charge_limit_kw: number | null;
  control_mode?: ControlMode;
  command_valid?: boolean;
}

export interface Frame {
  vehicle_id: string;
  t_sim_s: number;
  source: Source;
  running: boolean;
  mode: Mode;
  status_note: string;
  risk_status: RiskStatus;
  risk_reasons: string[];
  current_a: number;
  power_kw: number; // + 충전, - 방전
  current_soc: number;
  c_rate: number;
  heat_kw: number;
  coolant_in_c: number;
  coolant_out_c?: number;
  chiller_kw?: number;
  cooling_kw?: number;
  flow?: { velocity_ms: number; reynolds: number; dp_kpa: number; pump_electric_w: number; flow_lpm: number; flow_requested_lpm?: number };
  kpi: {
    core_max_c: number;
    core_hybrid_max_c: number;
    core_nn_max_c: number;
    surf_max_c: number;
    sensor_max_c: number | null;
    delta_t_k: number;
    hotspot: Cell;
    model_gap_k: number;
    n_sensors: number;
    mean_std_k: number;
  };
  field: {
    core: Grid; core_nn: Grid; surf: Grid; std: Grid; k_cells: Grid; q: Grid;
    true_core?: Grid; true_surf?: Grid;
  };
  sensors: Cell[];
  twin: {
    soh_est: number | null;      // null = 아직 추정 중 (갱신 조건을 만족한 데이터 부족)
    soh_lo: number | null;       // 95 % 신뢰구간
    soh_hi: number | null;
    soh_status: string;
    soh_updates: number;
    soh_skipped: number;
    soh_raw: number;             // v0.4 이전 방식 (비교용)
    weak_cells: Cell[];
    k_mean: number;
    residual_rmse: number | null;
    R_in: number;
    C_core: number;
    pinn_ok: boolean;
    soh_true?: number;
    core_rmse_k?: number;
    core_nn_rmse_k?: number;
    core_max_err_k?: number;
    true_core_max_c?: number;
    true_weak_cells?: Cell[];
  };
  forecast: { horizon_min: number; series: number[]; peak_c: number | null; minutes_to_warning: number | null };
  runaway: Runaway;
  commands: Commands;
  controls: Controls;
  control: ControlStatus;
  input: InputPriority;
  faults?: {
    bad_sensors: Cell[];          // 차갑게 읽음·고착 → 보정에서 제외
    local_sensors: Cell[];        // 주변보다 뜨겁게 읽음 → 그 셀에만 반영 + 현장 확인
    bad_reasons: Record<string, string>;
    bad_sensors_ever: Cell[];
    sensor_z: Record<string, number>;
    n_used: number;
  };
  ingest: { connected: number; last_rx: number | null; samples: number; rejected: number; warnings: string[]; age_s: number | null };
  log_rows: number;
  fleet?: { view: string; vehicles: FleetVehicle[] };
  security?: { auth_required: boolean; db_enabled: boolean };
  replay?: {
    name: string; title: string; row: number; rows: number;
    available_probes: Cell[]; holdout_probes: Cell[];
    holdout_rmse_k: number | null; mean_baseline_rmse_k: number | null;
    probe_values: Record<string, number>;
    citation: string;
  };
}

export interface Dataset {
  name: string; title: string; rows: number; duration_min: number; soc_start: number; soc_end: number;
  current_mean_a: number; probe_max_start_c: number; probe_max_peak_c: number; source: string; license: string; citation: string;
}

export interface SweepPoint {
  id: number; width_mm: number; depth_mm: number; flow_lpm: number;
  peak_core_c: number; peak_delta_t_k: number; pump_w: number; pumping_w: number; dp_kpa: number;
  reynolds: number; h: number; pump_energy_wh: number; pareto_tmax: boolean; pareto_dt: boolean;
  dp_total_kpa: number; dp_friction_kpa: number; dp_bend_kpa: number; dp_port_kpa: number;
  pump_speed: number; pump_eta: number; feasible: boolean; infeasible_reason: string; pareto_3d: boolean; dt_ok: boolean;
}

export interface SweepBrief {
  id: number; width_mm: number; depth_mm: number; flow_lpm: number; peak_core_c: number; peak_delta_t_k: number;
  pump_w: number; dp_total_kpa: number; pump_speed: number; feasible: boolean; infeasible_reason: string;
}

export interface SweepResult {
  conditions: { charge_power_kw: number; ambient_c: number; coolant_set_c: number; soh: number };
  charge_time_min: number; n_cases: number; current_id: number | null;
  knee_tmax_id: number; knee_dt_id: number; elapsed_s: number; points: SweepPoint[];
  pump_model: { h0_kpa: number; q_max_lpm: number; eta_hyd_peak: number; loop_dp_kpa_at_20lpm: number; plate_dp_limit_kpa: number; assumed: boolean };
  n_feasible: number; dt_target_k: number; n_dt_ok_feasible: number;
  recommend_3d: SweepBrief | null; old_recommend: SweepBrief | null;
  loss_sensitivity: { case: string; n_feasible: number; recommend: { width_mm: number; depth_mm: number; flow_lpm: number } | null;
    base_rec_still_feasible: boolean | null; base_rec_still_pareto: boolean | null }[];
  cfd_plan: { candidates: (SweepBrief & { role: string })[]; compare: string[]; setup: string; accept: string };
}

export interface EvalSet {
  pinn_rmse: number; pinn_max: number; data_only_rmse: number; data_only_max: number;
  datasheet_physics_rmse: number; datasheet_physics_max: number; learned_physics_rmse: number;
  surface_as_core_rmse: number; peak_core_true: number; peak_core_pinn: number;
}

export interface SohCase {
  true: number; ambient_c: number; flow_lpm: number; power_kw: number;
  new: number | null; lo: number | null; hi: number | null; old: number;
  updates: number; skipped: number; core_max_c: number; covered: boolean;
}
export interface RobustResult {
  core_rmse_k: number; hottest_cell_underestimate_k: number; peak_true_c: number; peak_est_c: number;
  fault_cell_max_underestimate_k: number | null; sensor_flagged_after_s: number | null; false_sensor_flags: number;
}
export interface SohSummary { max_abs_err?: number; spread?: number; mean_err?: number; n?: number }

export interface ModelsReport {
  pinn: null | {
    trained_at: string;
    train: { scenarios: number; labeled_scenarios: number; pairs: number; labeled_pairs: number; iters: number; seconds: number };
    params: { R_in_datasheet: number; R_in_learned: number; R_in_true: number; C_datasheet: number; C_learned: number; C_true: number };
    eval: { test: EvalSet; ood: EvalSet };
    history: { it: number; data: number; phys: number; C: number; R_in: number }[];
    arch: { features: string[]; layers: number[] };
  };
  placement: null | {
    counts: number[]; greedy: number[]; uniform: number[]; random: number[]; prior_rmse: number;
    layouts: { greedy: Record<string, Cell[]>; uniform: Record<string, Cell[]> };
    sim_validation?: {
      counts: number[];
      with_voltage: { greedy: { core_rmse: number; surf_rmse: number }[]; uniform: { core_rmse: number; surf_rmse: number }[] };
      temp_only: { uniform: { core_rmse: number; surf_rmse: number }[] };
    };
  };
  robustness?: null | {
    generated_at: string; conditions: string; finding: string; tried_not_adopted: string[];
    rows: { name: string; kind: string; before: RobustResult; after: RobustResult }[];
    cooling_coverage: { n_sensors: number; where: string; cell: Cell; fault_cell_max_underestimate_k: number | null }[];
  };
  soh_sensitivity?: null | {
    generated_at: string; method: string;
    grid_soh85: SohCase[]; levels: SohCase[]; harsh: SohCase[];
    summary: { new: SohSummary; old: SohSummary; coverage: string };
  };
  real_validation: null | Record<string, {
    title: string;
    by_n: { n_use: number; n_holdout: number; rmse: number; max_err: number; mean_baseline_rmse: number; nearest_baseline_rmse: number }[];
  }>;
}

export const T_CORE_WARNING_C = 45;
export const T_CORE_DANGER_C = 55;
export const DELTA_T_TARGET_K = 5;

export const DEFAULT_CONTROLS: Controls = {
  source: 'sim',
  dataset: 'ev_charge_strong',
  mode: 'fast_charge',
  charge_power_kw: 150,
  ambient_c: 25,
  coolant_set_c: 20,
  coolant_flow_lpm: 10,
  channel_width_mm: 8,
  channel_depth_mm: 3,
  true_soh: 0.85,
  derating: true,
  derate_source: 'twin',
  auto_pump: true,
  use_voltage: true,
  time_scale: 30,
  force_fallback: false,
};

export const CONTROL_MODE_META: Record<ControlMode, { label: string; color: string; icon: string }> = {
  twin: { label: 'AI 계산으로 보호 중', color: 'var(--color-good)', icon: '●' },
  fallback: { label: '안전 모드 (단순 규칙으로 보호 중)', color: 'var(--color-serious)', icon: '◆' },
  off: { label: '자동 보호 꺼짐 (보기만)', color: 'var(--color-muted)', icon: '○' },
};

export const MODE_LABEL: Record<Mode, string> = {
  fast_charge: '급속충전', highway: '고속도로', city: '도심', aggressive: '거친 운전', rest: '쉬는 중',
};
export const SOURCE_LABEL: Record<Source, string> = { sim: '가상 차량', replay: '실차 기록 재생', ingest: '실제 차량 연결' };
