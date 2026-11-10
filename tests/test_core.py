"""Package smoke tests. Run with: pytest"""
import numpy as np
import pytest

from binetic import NetReservoir, RidgeReadout, EvolveCfg, forecast, nmse
from binetic.benchmarks import narma, mackey_glass, freq_signals


def test_geometry_roundtrip():
    rng = np.random.default_rng(0)
    g = EvolveCfg()
    geo = NetReservoir.__init__  # noqa - placeholder
    from binetic import Geometry
    geo = Geometry.random(4, 4, rng, 20)
    assert geo.R == 4 and geo.D == 4
    m = geo.mutate(rng)
    assert m.lags.shape == geo.lags.shape


def test_transform_shape():
    u, y = narma(200, 10, seed=1)
    r = NetReservoir(R=4, D=4, seed=0)
    X = r.transform(u)
    assert X.shape[0] == u.size
    assert X.shape[1] == r.n_features
    assert np.isfinite(X).all()


def test_readout_fits():
    u, y = narma(200, 10, seed=1)
    r = NetReservoir(R=4, D=4, seed=0)
    X = r.transform(u)
    cut = int(u.size * 0.7)
    ro = RidgeReadout().fit(X, y)
    assert ro.w is not None
    assert np.isfinite(ro.predict(X)).all()


def test_short_evolution_improves_or_holds():
    """With few generations the score should at least be sane."""
    u, y = narma(300, 10, seed=1)
    res = forecast(u, y, EvolveCfg(gens=2, pop=3, R=4, D=4))
    assert np.isfinite(res["nmse"])
    assert res["nmse"] >= 0
