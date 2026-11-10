# binetic

A reservoir computing core where the reservoir is a set of **lattice
registers** — each one holds the outer product of trigonometric phase
vectors read from its own delay taps, so the state contains genuine
nonlinear mixings of input history.

The register wiring (delays, decays, phase offsets, scalings) is a
**genome evolved against readout error**, not fixed at random.

## Install

```bash
pip install .
```

Requires numpy. Python 3.8+.

## Quick start

```python
import numpy as np
from binetic import NetReservoir, EvolveCfg, forecast
from binetic.benchmarks import narma

u, y = narma(3000, 10, seed=1)

# Evolve the register wiring against readout error
res = forecast(u, y, EvolveCfg(gens=8, pop=6))
print(res["nmse"])           # NARMA-10 validation NMSE
print(res["geometry"])       # the evolved wiring
```

## Use it as a substrate in your own pipeline

```python
from binetic import NetReservoir, RidgeReadout

r = NetReservoir()                    # default random geometry
r.fit(u, y)                           # evolve wiring for this task
X = r.transform(u)                    # (T, n_features) state matrix

readout = RidgeReadout().fit(X, y)    # lambda chosen by validation
pred = readout.predict(X)
```

The state matrix is just numpy — feed it to any regressor or classifier.

## The physical mapping

One register is one protocol field sliced into a multidimensional relay.
`sin`/`cos` are the trig logic gates (XOR and AND are projections of the
same rotation, so one accumulated phase yields both at unit depth). The
outer product is the gate cascade. The delay taps are packets in flight.

Nothing here requires a P4 switch. The same mathematics runs in pure
numpy. Where a P4 switch is available, `binetic.net` runs the substrate
through real hardware via packet timing and payload receipts.

## What the numbers say

NARMA-30 validation NMSE against a 200-node echo-state network on the
same data: the ESN scores ~0.55, the evolved lattice ~0.025. Full
benchmark suite:

```bash
python -m binetic.benchmarks
```

## Status

Alpha. The software core is measured and reproducible. The P4 substrate
is a working prototype on BMv2. See `WHITEPAPER.md`.
