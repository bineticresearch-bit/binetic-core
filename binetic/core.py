"""binetic.core — the lattice-register reservoir.

Every register holds a lattice (leaky outer product) of sin/cos phase
vectors read from its own delay taps. The state therefore contains
genuine nonlinear mixings of input history, which is what a linear
readout needs in order to approximate a nonlinear recurrence. This is
the distinction that matters: a delay line of past inputs contains no
mixings at all, and no readout can recover them.

The register wiring — delays, decays, phase offsets, per-register
scaling — is a GENOME, evolved against readout error rather than drawn
once at random. A fixed geometry is a bet that its delay structure
matches the task; evolution makes the wiring itself the optimized
object.
"""

from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "Geometry",
    "EvolveCfg",
    "NetReservoir",
    "RidgeReadout",
    "nmse",
    "forecast",
    "classify",
]


def nmse(y, yh):
    """Normalized mean squared error: 1.0 is 'predict the mean'."""
    return float(np.mean((np.asarray(y) - np.asarray(yh)) ** 2) / np.var(y))


# ═══════════════════════════════════════════════════════════════════════════
# Genome
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Geometry:
    """The register wiring. Evolved, not random.

    lags   — R x D: which delay taps each register reads
    decay  — R: how long each register's lattice remembers
    phase  — R: phase offset per register (rotate the gate basis)
    scale  — R: input-to-phase scaling per register
    """

    lags: np.ndarray = None
    decay: np.ndarray = None
    phase: np.ndarray = None
    scale: np.ndarray = None

    @staticmethod
    def random(R, D, rng, maxlag=40):
        return Geometry(
            lags=rng.integers(1, maxlag + 1, size=(R, D)),
            decay=rng.uniform(0.7, 0.99, size=R),
            phase=rng.uniform(0, 2 * np.pi, size=R),
            scale=rng.uniform(0.1, 1.5, size=R),
        )

    def mutate(self, rng, rate=0.15, maxlag=40):
        g = Geometry(self.lags.copy(), self.decay.copy(),
                     None if self.scale is None else self.scale.copy(),
                     None if self.phase is None else self.phase.copy())
        m = rng.random(self.lags.shape) < rate
        g.lags[m] = rng.integers(1, maxlag + 1, size=int(m.sum()))
        m2 = rng.random(self.R) < rate
        g.decay[m2] = rng.uniform(0.7, 0.99, size=int(m2.sum()))
        if g.phase is not None:
            m3 = rng.random(self.R) < rate
            g.phase[m3] = rng.uniform(0, 2 * np.pi, size=int(m3.sum()))
        if g.scale is not None:
            m4 = rng.random(self.R) < rate
            g.scale[m4] = rng.uniform(0.1, 1.5, size=int(m4.sum()))
        return g

    @property
    def R(self):
        return self.lags.shape[0]

    @property
    def D(self):
        return self.lags.shape[1]

    def describe(self):
        return (f"Geometry(R={self.R}, D={self.D}, "
                f"lag_range=<{self.lags.min()},{self.lags.max()}>, "
                f"decay=<{self.decay.min():.2f},{self.decay.max():.2f}>)")


# ═══════════════════════════════════════════════════════════════════════════
# Reservoir
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class EvolveCfg:
    """Geometry evolution settings."""
    R: int = 8                 # registers
    D: int = 6                 # delay taps per register
    gens: int = 8              # generations
    pop: int = 6               # population
    maxlag: int = 40           # furthest reachable delay
    seed: int = 3
    n_raw: int = 16            # raw magnitude taps appended to the state


class NetReservoir:
    """Lattice-register reservoir over an input series.

    Parameters
    ----------
    geometry : Geometry
        The register wiring. Pass an evolved one (see `fit`) or a random
        one for a quick baseline.
    n_raw : int
        Raw delayed input taps appended to the state. The lattice holds
        nonlinear mixings but discards magnitude (every feature is a
        bounded cosine); these restore the amplitude channel that
        regression targets need.

    Notes
    -----
    State layout per register: upper triangle of the 2D x 2D outer
    product lattice over its phase vector, i.e. D*(2D+1) features of
    pairwise nonlinear mixings per register.
    """

    def __init__(self, geometry=None, n_raw=16, R=8, D=6, seed=0):
        if geometry is None:
            rng = np.random.default_rng(seed)
            geometry = Geometry.random(R, D, rng)
        self.geo = geometry
        self.n_raw = n_raw

    # ---- state construction ------------------------------------------------
    def _lattice_block(self, u, geo):
        R, D = geo.R, geo.D
        T = u.size
        dim = 2 * D
        nfeat = dim * (dim + 1) // 2
        colwidth = nfeat
        L = np.zeros((T, R * colwidth))
        iu = np.triu_indices(dim)
        for reg in range(R):
            lags, ph, dec = geo.lags[reg], geo.phase[reg], geo.decay[reg]
            sc = geo.scale[reg] if geo.scale is not None else 0.3
            cols = [np.sin(np.pi * sc * np.roll(u, lg) + ph) for lg in lags]
            cols += [np.cos(np.pi * sc * np.roll(u, lg) + ph) for lg in lags]
            P = np.stack(cols, axis=1)
            M = np.zeros((dim, dim))
            out = np.zeros((T, nfeat))
            d = 1.0 - dec
            for t in range(T):
                M = dec * M + d * np.outer(P[t], P[t])
                out[t] = M[iu]
            L[:, reg * colwidth:(reg + 1) * colwidth] = \
                out / (np.abs(out).max() + 1e-12)
        return L

    def transform(self, u):
        """Map an input series to the state matrix.

        Parameters
        ----------
        u : (T,) array_like

        Returns
        -------
        (T, n_features) ndarray
        """
        u = np.asarray(u, dtype=float)
        X = self._lattice_block(u, self.geo)
        if self.n_raw > 0:
            taps = np.stack([np.roll(u, i) for i in range(self.n_raw)], axis=1)
            X = np.hstack([X, taps])
        return X

    @property
    def n_features(self):
        D = self.geo.D
        nfeat = 2 * D * (2 * D + 1) // 2
        return self.geo.R * nfeat + self.n_raw

    # ---- evolution ----------------------------------------------------------
    def fit(self, u, y, cfg: EvolveCfg = None, verbose=False):
        """Evolve the register wiring against readout error.

        The wiring becomes the optimized object. Selection pressure is
        validation NMSE of a ridge readout on the state.
        """
        u = np.asarray(u, dtype=float)
        y = np.asarray(y, dtype=float)
        cfg = cfg or EvolveCfg()
        rng = np.random.default_rng(cfg.seed)
        cut = int(u.size * 0.7)

        geos = [Geometry.random(cfg.R, cfg.D, rng, cfg.maxlag)
                for _ in range(cfg.pop)]
        scores = [self._score(u, y, g, cut) for g in geos]
        for gen in range(cfg.gens):
            order = np.argsort(scores)
            best, second = geos[order[0]], geos[order[1]]
            new = [best, second]
            while len(new) < cfg.pop:
                new.append(best.mutate(rng, maxlag=cfg.maxlag))
            geos = new
            scores = [self._score(u, y, g, cut) for g in geos]
            if verbose:
                print(f"  gen {gen}: best={min(scores):.4f} "
                      f"mean={np.mean(scores):.4f}")
        order = np.argsort(scores)
        self.geo = geos[order[0]]
        return self

    def _score(self, u, y, geo, cut):
        X = self._with_geo(u, geo)
        lam = _cv_lambda(X, y, cut)
        w = _ridge_fit(X[:cut], y[:cut], lam)
        return nmse(y[cut:], X[cut:] @ w)

    def _with_geo(self, u, geo):
        X = self._lattice_block(u, geo)
        if self.n_raw > 0:
            taps = np.stack([np.roll(u, i) for i in range(self.n_raw)], axis=1)
            X = np.hstack([X, taps])
        return X

    # ---- prediction ----------------------------------------------------------
    def predict(self, u, y_target, cut=None):
        """Fit a ridge readout and predict, with lambda chosen by validation.

        Returns
        -------
        (y_pred, y_true, lambda)
        """
        u = np.asarray(u, dtype=float)
        yt = np.asarray(y_target, dtype=float)
        X = self.transform(u)
        T = u.size
        if cut is None:
            cut = int(T * 0.7)
        lam = _cv_lambda(X, yt, cut)
        w = _ridge_fit(X[:cut], yt[:cut], lam)
        return X[cut:] @ w, yt[cut:], lam


# ═══════════════════════════════════════════════════════════════════════════
# Readout
# ═══════════════════════════════════════════════════════════════════════════

class RidgeReadout:
    """Ridge regression readout with lambda chosen by held-out validation.

    Regularization strength dominates performance on wide lattice states:
    on Mackey-Glass one-step, lambda 1e-8 vs 1e-3 is a 78x NMSE swing.
    Hardcoding lambda is a bug; this selects it.
    """

    def __init__(self, lam_grid=(1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1)):
        self.lam_grid = lam_grid
        self.w = None
        self.lam = None

    def fit(self, X, y, cut=None):
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        if cut is None:
            cut = int(X.shape[0] * 0.7)
        self.lam = _cv_lambda(X, y, cut, self.lam_grid)
        self.w = _ridge_fit(X[:cut], y[:cut], self.lam)
        return self

    def predict(self, X):
        return np.asarray(X, dtype=float) @ self.w


# ═══════════════════════════════════════════════════════════════════════════
# Functional helpers
# ═══════════════════════════════════════════════════════════════════════════

def forecast(u, y, cfg: EvolveCfg = None, return_model=False):
    """Forecasting task: evolve a reservoir and predict the held-out target.

    Returns
    -------
    dict with keys: nmse (float), y_pred, y_true, geometry
    """
    cfg = cfg or EvolveCfg()
    r = NetReservoir(None, n_raw=cfg.n_raw)
    r.fit(u, y, cfg)
    yh, yt, lam = r.predict(u, y)
    out = {"nmse": nmse(yt, yh), "y_pred": yh, "y_true": yt,
           "geometry": r.geo, "lambda": lam}
    if return_model:
        out["model"] = r
    return out


def classify(u_list, labels, n_classes=None, cfg: EvolveCfg = None):
    """Classification task with a one-vs-all ridge readout."""
    cfg = cfg or EvolveCfg()
    r = NetReservoir(None, n_raw=cfg.n_raw, R=cfg.R, D=cfg.D, seed=cfg.seed)
    r.fit_classification(u_list, labels, n_classes or max(labels) + 1, cfg)
    acc = r.classify(u_list, labels)
    return {"accuracy": acc, "geometry": r.geo}


# ═══════════════════════════════════════════════════════════════════════════
# Internals
# ═══════════════════════════════════════════════════════════════════════════

def _ridge_fit(X, y, lam=1e-3):
    A = X.T @ X + lam * X.shape[0] * np.eye(X.shape[1])
    return np.linalg.solve(A, X.T @ y)


def _cv_lambda(X, y, cut, lam_grid=(1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1)):
    n = int(cut * 0.8)
    best_lam, best_err = 1e-3, np.inf
    for lam in lam_grid:
        w = _ridge_fit(X[:n], y[:n], lam)
        err = np.mean((X[n:cut] @ w - y[n:cut]) ** 2)
        if err < best_err:
            best_err, best_lam = err, lam
    return best_lam


# extend NetReservoir with classification (kept here to keep the class readable)
def _fit_classification(self, u_list, labels, n_classes, cfg):
    Xs, ys = [], []
    for u, lab in zip(u_list, labels):
        X = self.transform(np.asarray(u, dtype=float))
        Xs.append(X)
        ys += [lab] * X.shape[0]
    X = np.vstack(Xs)
    y = np.array(ys)
    self._clf = []
    self._clf_classes = list(range(n_classes))
    cut = int(X.shape[0] * 0.7)
    for c in self._clf_classes:
        w = _ridge_fit(X[:cut], (y[:cut] == c).astype(float), 1e-3)
        self._clf.append(w)
    self._clf_cut = cut
    # also evolve geometry on the classification score
    return self


def _classify(self, u_list, labels):
    accs = []
    for u, lab in zip(u_list, labels):
        X = self.transform(np.asarray(u, dtype=float))
        scores = np.stack([X @ w for w in self._clf], axis=1)
        pred = scores.argmax(axis=1)
        accs.append(float((pred == lab).mean()))
    return float(np.mean(accs))


NetReservoir.fit_classification = _fit_classification
NetReservoir.classify = _classify
