"""binetic.benchmarks — reproducible task suite.

Every number that goes into the whitepaper comes from here, so these are
the claims the package has to back up.
"""
import numpy as np

from .core import (
    EvolveCfg, Geometry, NetReservoir, RidgeReadout,
    nmse, _ridge_fit, _cv_lambda,
)

__all__ = [
    "narma", "mackey_glass", "freq_signals",
    "bench_forecast", "bench_esn", "bench_full",
]


# ═══════════════════════════════════════════════════════════════════════════
# Signal generators
# ═══════════════════════════════════════════════════════════════════════════

def narma(n, order=10, seed=1):
    """NARMA system. The standard hard benchmark for reservoir computing."""
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 0.5, size=n + order)
    y = np.zeros(n + order)
    y[:order] = 0.1
    for t in range(order, n + order):
        s = 0.3 if order == 10 else 0.2
        y[t] = (s * y[t-1] + y[t-1] / (1.0 + y[t-1]**2)
                + 1.5 * u[t-order] * u[t-1]
                + 0.001 * np.sum(np.abs(y[t-order:t])) + 0.1)
    return u[order:], y[order:]


def mackey_glass(n, tau=17, beta=2.0, gamma=1.0, dt=0.1, nstep=1, seed=0):
    """Discretized Mackey-Glass via ring-buffer history."""
    rng = np.random.default_rng(seed)
    h = dt / nstep
    size = int(round(tau / h)) + 1
    hist = 1.2 + 0.01 * rng.standard_normal(size)
    x = hist[-1]
    xs = np.zeros(n)
    for i in range(n * nstep):
        i0 = i % size
        xtau = hist[i0]
        x += h * (beta * xtau / (1.0 + xtau**10) - gamma * x)
        hist[i0] = x
        if (i + 1) % nstep == 0:
            xs[(i + 1) // nstep - 1] = x
    return xs


def freq_signals(n=1200, periods=(24.0, 40.0, 64.0), per_class=4, seed=0):
    """Frequency discrimination: slow/mid/fast pseudo-periodic signals."""
    U, L = [], []
    for c, p in enumerate(periods):
        for s in range(per_class):
            rng = np.random.default_rng(seed + 100 * c + s)
            t = np.arange(n)
            sig = np.sin(2 * np.pi * t / p) + 0.2 * rng.standard_normal(n)
            U.append(np.clip(0.5 + 0.35 * sig, 0.0, 1.0))
            L.append(c)
    return U, L


# ═══════════════════════════════════════════════════════════════════════════
# Benchmarks
# ═══════════════════════════════════════════════════════════════════════════

def bench_forecast(order=10, seed=1, n=3000, cfg=None, verbose=False):
    """NARMA forecasting with geometry evolution."""
    u, y = narma(n, order, seed=seed)
    cfg = cfg or EvolveCfg()
    r = NetReservoir(None, n_raw=cfg.n_raw)
    r.fit(u, y, cfg, verbose=verbose)
    yh, yt, lam = r.predict(u, y)
    return {"task": f"NARMA-{order}", "nmse": nmse(yt, yh),
            "y_pred": yh, "y_true": yt, "geometry": r.geo, "lambda": lam}


def bench_esn(order=30, seed=3, n=3000, n_res=200):
    """Echo-state network baseline, same data, ridge on reservoir state."""
    u, y = narma(n, order, seed=1)
    rng = np.random.default_rng(seed)
    W = np.zeros((n_res, n_res))
    mask = rng.random((n_res, n_res)) < 0.15
    W[mask] = rng.standard_normal(int(mask.sum()))
    W *= 0.9 / max(abs(np.linalg.eigvals(W)))
    Win = rng.standard_normal(n_res)
    x = np.zeros(n_res)
    X = np.empty((u.size, n_res + 2))
    for t in range(u.size):
        x = 0.7 * x + 0.3 * np.tanh(Win * u[t] + W @ x)
        X[t] = np.concatenate([[1.0, u[t]], x])
    cut = int(u.size * 0.7)
    w = _ridge_fit(X[:cut], y[:cut], 1e-6)
    return {"task": f"NARMA-{order} ESN{n_res}",
            "nmse": nmse(y[cut:], X[cut:] @ w)}


def bench_full(n=3000, verbose=True):
    """Run the complete suite. Returns a results dict."""
    res = {}

    for order in (10, 30):
        r = bench_forecast(order, n=n, verbose=verbose)
        res[f"narma{order}"] = r["nmse"]
        if verbose:
            print(f"NARMA-{order}: NMSE = {r['nmse']:.4f}")

    res["narma30_esn200"] = bench_esn(30, n=n)["nmse"]

    # Mackey-Glass one-step
    mg = mackey_glass(n, seed=7)
    u, y = mg[:-1], mg[1:]
    r = NetReservoir(None, n_raw=16)
    r.fit(u, y, EvolveCfg(seed=3), verbose=verbose)
    yh, yt, lam = r.predict(u, y)
    res["mg_onestep"] = nmse(yt, yh)

    # Frequency 3-class
    U, L = freq_signals(seed=5)
    r = NetReservoir(None, n_raw=16, R=8, D=6, seed=5)
    out = r.fit_classification(U, L, 3, EvolveCfg(seed=5)) if False else None
    # use functional path
    from .core import classify
    out = classify(U, L, 3, EvolveCfg(seed=5))
    res["freq3"] = out["accuracy"]

    return res
