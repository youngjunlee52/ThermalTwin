"""
PINN 코어온도 가상센서 (Physics-Informed Neural Network) — NumPy 로 직접 구현 (설치 부담 없음)

목표: 측정 가능한 값(셀 표면온도 Ts, 셀 발열 q)만으로 측정 불가능한 코어온도 Tc 를 추정
      Tc = Ts + g_θ(특징)          g_θ: 신경망 (12 → 32 → 32 → 1, tanh)

손실 = 데이터 손실 + 물리 손실
  · 데이터 손실: 코어 열전대가 있는 '실험실 셀' 데이터(일부 시나리오만)에서 (g − (Tc − Ts))²
  · 물리 손실: 모든 데이터(라벨 없는 주행 데이터 포함)에서 코어 열전달 식의 잔차
        C · dTc/dt − [ q − (Tc − Ts) / R_in ] = 0
    C(코어 열용량), R_in(코어→표면 열저항)도 학습 변수 → 데이터시트 값이 틀려도 실제 값을 역추정
    (Wang et al. 2023 BINN 이 물리 파라미터를 신경망과 같이 학습한 것과 같은 구조)

특징 (셀마다): Ts, q, q 의 지수이동평균 4개, dTs/dt 의 지수이동평균 4개(60·200·600·1500 s), SOC, |C-rate|
  → 코어 ODE 의 정확한 해는 Tc − Ts = R·[q − C·dTs/dt] 를 시간상수 τ=R·C 로 거른 것이므로,
    여러 시간상수의 이동평균을 주면 신경망이 τ 를 몰라도 이 관계를 배울 수 있음
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import thermal_model as tm
from paths import MODEL_DIR
EMA_TAUS = (60.0, 200.0, 600.0, 1500.0)
N_FEAT = 12
HIDDEN = 32
Q_SCALE = 40.0   # 물리 잔차 정규화 [W]


# ─────────────────────────────────────────────
# 특징
# ─────────────────────────────────────────────
class FeatureState:
    """실시간용: 셀마다 지수이동평균 상태를 들고 있다가 샘플이 올 때마다 갱신"""

    def __init__(self, n_cells: int = tm.N_CELLS):
        self.ema_q = np.zeros((len(EMA_TAUS), n_cells))
        self.ema_d = np.zeros((len(EMA_TAUS), n_cells))   # dTs/dt [K/min]
        self.prev_ts = None

    def update(self, ts: np.ndarray, q: np.ndarray, soc: float, c_rate: float, dt: float) -> np.ndarray:
        for k, tau in enumerate(EMA_TAUS):
            a = 1.0 - np.exp(-dt / tau)
            self.ema_q[k] += a * (q - self.ema_q[k])
        if self.prev_ts is not None and dt > 0:
            rate = (ts - self.prev_ts) / dt * 60.0
            for k, tau in enumerate(EMA_TAUS):
                a = 1.0 - np.exp(-dt / tau)
                self.ema_d[k] += a * (rate - self.ema_d[k])
        self.prev_ts = ts.copy()
        n = ts.shape[0]
        return np.column_stack([ts, q, *self.ema_q, *self.ema_d, np.full(n, soc), np.full(n, abs(c_rate))])

    def warm_start(self, q: np.ndarray) -> None:
        self.ema_q[:] = q


def sequence_features(ts: np.ndarray, q: np.ndarray, soc: np.ndarray, c_rate: np.ndarray, dt: float) -> np.ndarray:
    """학습용: 시계열 (T, B, C) → 특징 (T, B, C, 9)"""
    T = ts.shape[0]
    st = FeatureState(int(np.prod(ts.shape[1:])))
    out = np.empty(ts.shape + (N_FEAT,))
    for t in range(T):
        out[t] = st.update(ts[t].ravel(), q[t].ravel(), 0.0, 0.0, dt).reshape(ts.shape[1:] + (N_FEAT,))
        out[t, ..., 10] = soc[t][:, None, None]
        out[t, ..., 11] = np.abs(c_rate[t])[:, None, None]
    return out


# ─────────────────────────────────────────────
# 신경망 (수동 역전파)
# ─────────────────────────────────────────────
class MLP:
    def __init__(self, rng: np.random.Generator | None = None):
        rng = rng or np.random.default_rng(0)
        self.p = {
            "W1": rng.normal(0, 1 / np.sqrt(N_FEAT), (N_FEAT, HIDDEN)), "b1": np.zeros(HIDDEN),
            "W2": rng.normal(0, 1 / np.sqrt(HIDDEN), (HIDDEN, HIDDEN)), "b2": np.zeros(HIDDEN),
            "W3": rng.normal(0, 0.1 / np.sqrt(HIDDEN), (HIDDEN, 1)), "b3": np.zeros(1),
        }
        self.mu = np.zeros(N_FEAT)
        self.sd = np.ones(N_FEAT)
        self.out_scale = 1.0

    def forward(self, x: np.ndarray, cache: bool = False):
        z = (x - self.mu) / self.sd
        h1 = np.tanh(z @ self.p["W1"] + self.p["b1"])
        h2 = np.tanh(h1 @ self.p["W2"] + self.p["b2"])
        y = (h2 @ self.p["W3"] + self.p["b3"])[:, 0] * self.out_scale
        return (y, (z, h1, h2)) if cache else y

    def backward(self, cache, dy: np.ndarray) -> dict:
        z, h1, h2 = cache
        dy = dy[:, None] * self.out_scale
        g = {"W3": h2.T @ dy, "b3": dy.sum(0)}
        dh2 = dy @ self.p["W3"].T * (1 - h2**2)
        g["W2"], g["b2"] = h1.T @ dh2, dh2.sum(0)
        dh1 = dh2 @ self.p["W2"].T * (1 - h1**2)
        g["W1"], g["b1"] = z.T @ dh1, dh1.sum(0)
        return g


class Adam:
    def __init__(self, params: dict, lr: float = 3e-3):
        self.lr, self.t = lr, 0
        self.m = {k: np.zeros_like(v) for k, v in params.items()}
        self.v = {k: np.zeros_like(v) for k, v in params.items()}

    def step(self, params: dict, grads: dict, lr: float | None = None) -> None:
        self.t += 1
        lr = lr or self.lr
        for k in params:
            if k not in grads:
                continue
            self.m[k] = 0.9 * self.m[k] + 0.1 * grads[k]
            self.v[k] = 0.999 * self.v[k] + 0.001 * grads[k] ** 2
            mh = self.m[k] / (1 - 0.9**self.t)
            vh = self.v[k] / (1 - 0.999**self.t)
            params[k] -= lr * mh / (np.sqrt(vh) + 1e-8)


# ─────────────────────────────────────────────
# 학습
# ─────────────────────────────────────────────
def make_pairs(ens: dict, lag: int, rng: np.random.Generator, frac: float):
    """앙상블 기록 → (특징 t, 특징 t+lag, Ts, Tc, 구간 평균 q) 샘플"""
    dt = float(ens["dt_sample"])
    cap = tm.CELL_CAPACITY_AH * ens["soh"]
    c_rate = ens["current"] / cap[None, :]
    noise_ts = ens["ts"] + rng.normal(0, 0.1, ens["ts"].shape)       # 추정기 출력 수준의 잡음
    feat = sequence_features(noise_ts, ens["q"], ens["soc"], c_rate, dt)   # (T,B,8,12,9)
    T, B = feat.shape[:2]
    feat = feat.reshape(T, B, -1, N_FEAT)
    ts = ens["ts"].reshape(T, B, -1)
    tc = ens["tc"].reshape(T, B, -1)
    q = ens["q"].reshape(T, B, -1)
    warm = int(300 / dt)                                               # 이동평균이 자리잡은 뒤부터
    t0 = np.arange(warm, T - lag)
    qbar = np.stack([q[t:t + lag + 1].mean(0) for t in t0])            # (n_t, B, C)
    idx = np.stack(np.meshgrid(np.arange(len(t0)), np.arange(B), np.arange(ts.shape[2]), indexing="ij"), -1).reshape(-1, 3)
    idx = idx[rng.random(len(idx)) < frac]
    ti, bi, ci = idx[:, 0], idx[:, 1], idx[:, 2]
    tt = t0[ti]
    return {
        "x0": feat[tt, bi, ci], "x1": feat[tt + lag, bi, ci],
        "ts0": ts[tt, bi, ci], "ts1": ts[tt + lag, bi, ci],
        "g0": tc[tt, bi, ci] - ts[tt, bi, ci], "g1": tc[tt + lag, bi, ci] - ts[tt + lag, bi, ci],
        "q": qbar[ti, bi, ci], "b": bi, "dt": dt * lag,
    }


def train(train_pairs: dict, labeled_mask: np.ndarray, use_physics: bool = True, iters: int = 4000,
          batch: int = 4096, seed: int = 0, log_every: int = 100, r_in0: float = tm.R_IN, c0: float = tm.C_CORE,
          fit_phys: bool = True):
    """fit_phys=False: R_in·C 를 r_in0·c0 로 고정하고 신경망만 학습 (셀 시험처럼 R_in·C 를 직접 식별한 경우)"""
    rng = np.random.default_rng(seed)
    net = MLP(rng)
    allx = np.concatenate([train_pairs["x0"], train_pairs["x1"]])
    net.mu, net.sd = allx.mean(0), allx.std(0) + 1e-6
    net.out_scale = float(np.std(train_pairs["g0"][labeled_mask]) + 1e-3) * 2
    phys = {"logC": np.array(np.log(c0)), "logR": np.array(np.log(r_in0))}
    params = {**net.p, **phys}
    opt = Adam(params, 3e-3)
    lab = np.nonzero(labeled_mask)[0]
    allidx = np.arange(len(labeled_mask))
    dt = train_pairs["dt"]
    hist = []
    for it in range(iters):
        lr = 3e-3 * (0.5 * (1 + np.cos(np.pi * it / iters)) * 0.95 + 0.05)
        grads = {k: np.zeros_like(v) for k, v in params.items()}
        # 데이터 손실 (라벨 있는 실험실 셀)
        bl = rng.choice(lab, size=min(batch // 2, len(lab)))
        y, cache = net.forward(train_pairs["x0"][bl], cache=True)
        e = y - train_pairs["g0"][bl]
        l_data = float(np.mean(e**2))
        gd = net.backward(cache, 2 * e / len(bl))
        for k in gd:
            grads[k] += gd[k]
        l_phys = 0.0
        if use_physics:
            bp = rng.choice(allidx, size=batch)
            C, R = float(np.exp(params["logC"])), float(np.exp(params["logR"]))
            g0, c0_ = net.forward(train_pairs["x0"][bp], cache=True)
            g1, c1_ = net.forward(train_pairs["x1"][bp], cache=True)
            dts_ = train_pairs["ts1"][bp] - train_pairs["ts0"][bp]
            res = (C * (dts_ + g1 - g0) / dt - train_pairs["q"][bp] + 0.5 * (g0 + g1) / R) / Q_SCALE
            l_phys = float(np.mean(res**2))
            w = 1.0
            dres = w * 2 * res / len(bp) / Q_SCALE
            g_a = net.backward(c1_, dres * (C / dt + 0.5 / R))
            g_b = net.backward(c0_, dres * (-C / dt + 0.5 / R))
            for k in g_a:
                grads[k] += g_a[k] + g_b[k]
            grads["logC"] = np.array(np.sum(dres * C * (dts_ + g1 - g0) / dt))
            grads["logR"] = np.array(np.sum(dres * (-0.5 * (g0 + g1) / R)))
        if not fit_phys:
            grads.pop("logC", None)
            grads.pop("logR", None)
        opt.step(params, grads, lr)
        if it % log_every == 0 or it == iters - 1:
            hist.append({"it": it, "data": round(l_data, 5), "phys": round(l_phys, 5),
                         "C": round(float(np.exp(params["logC"])), 1), "R_in": round(float(np.exp(params["logR"])), 4)})
    net.p = {k: params[k] for k in net.p}
    return net, {"C": float(np.exp(params["logC"])), "R_in": float(np.exp(params["logR"]))}, hist


def physics_core_rollout(ts: np.ndarray, q: np.ndarray, dt: float, r_in: float, c: float) -> np.ndarray:
    """물리 모델 기준선: 표면온도·발열을 알 때 코어 ODE 를 적분 (데이터시트 값 사용)"""
    tc = ts[0].copy()
    out = np.empty_like(ts)
    sub = 5
    h = dt / sub
    for t in range(ts.shape[0]):
        for _ in range(sub):
            tc = tc + h * (q[t] - (tc - ts[t]) / r_in) / c
        out[t] = tc
    return out


def evaluate(net: MLP, ens: dict) -> dict:
    """시나리오 전체 시계열에서 코어온도 오차 (PINN vs 데이터시트 물리 모델)"""
    dt = float(ens["dt_sample"])
    cap = tm.CELL_CAPACITY_AH * ens["soh"]
    c_rate = ens["current"] / cap[None, :]
    rng = np.random.default_rng(99)
    ts_meas = ens["ts"] + rng.normal(0, 0.1, ens["ts"].shape)
    feat = sequence_features(ts_meas, ens["q"], ens["soc"], c_rate, dt)
    T = feat.shape[0]
    warm = int(300 / dt)
    pred = ts_meas + net.forward(feat.reshape(-1, N_FEAT)).reshape(ens["ts"].shape)
    phys = physics_core_rollout(ts_meas, ens["q"], dt, tm.R_IN, tm.C_CORE)
    tc = ens["tc"]
    sl = slice(warm, T)
    err_p = pred[sl] - tc[sl]
    err_f = phys[sl] - tc[sl]
    gap = tc[sl] - ens["ts"][sl]
    return {
        "pinn_rmse": float(np.sqrt(np.mean(err_p**2))), "pinn_max": float(np.abs(err_p).max()),
        "phys_rmse": float(np.sqrt(np.mean(err_f**2))), "phys_max": float(np.abs(err_f).max()),
        "surface_only_rmse": float(np.sqrt(np.mean(gap**2))),
        "peak_core_true": float(tc.max()), "peak_core_pinn": float(pred.max()),
    }


def save(net: MLP, phys: dict, report: dict, path: Path | None = None) -> None:
    path = path or MODEL_DIR / "pinn.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, mu=net.mu, sd=net.sd, out_scale=net.out_scale, C=phys["C"], R_in=phys["R_in"], **net.p)
    (path.with_suffix(".json")).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


class PINNVirtualSensor:
    """실시간 추론용"""

    def __init__(self, path: Path | None = None):
        path = path or MODEL_DIR / "pinn.npz"
        self.ok = path.exists()
        self.net = MLP()
        self.C, self.R_in = tm.C_CORE, tm.R_IN
        self.report = {}
        if self.ok:
            d = np.load(path)
            self.net.p = {k: d[k] for k in ("W1", "b1", "W2", "b2", "W3", "b3")}
            self.net.mu, self.net.sd, self.net.out_scale = d["mu"], d["sd"], float(d["out_scale"])
            self.C, self.R_in = float(d["C"]), float(d["R_in"])
            rp = path.with_suffix(".json")
            if rp.exists():
                self.report = json.loads(rp.read_text(encoding="utf-8"))
        self.state = FeatureState()

    def reset(self) -> None:
        self.state = FeatureState()

    def predict(self, ts: np.ndarray, q: np.ndarray, soc: float, c_rate: float, dt: float) -> np.ndarray:
        x = self.state.update(ts, q, soc, c_rate, dt)
        if not self.ok:
            return ts + q * self.R_in      # 학습 파일이 없으면 정상상태 근사
        return ts + self.net.forward(x)
