"""binetic — a lattice-register reservoir computing core.

A reservoir substrate built on trigonometric logic gates: each register
holds the outer product of sin/cos phase vectors read from its own delay
taps, so the state contains genuine nonlinear mixings of input history.
The register wiring (delays, decays, phases, scalings) is a genome
evolved by readout error rather than fixed at random.

Two layers:
  binetic.core       — the software reservoir (pure numpy, no hardware)
  binetic.net        — run the same substrate through a real P4 switch
                       via packet timing, payload receipts, and mirror
                       triangulation

The physical story, for the record: one register is one protocol field
sliced into a multidimensional relay; sin/cos are the trig logic gates
(XOR and AND are projections of the same rotation); the outer product is
the gate cascade; the delay taps are packets in flight.
"""

from .core import (
    Geometry,
    EvolveCfg,
    NetReservoir,
    RidgeReadout,
    nmse,
    forecast,
    classify,
)

__version__ = "0.1.0"

__all__ = [
    "Geometry",
    "EvolveCfg",
    "NetReservoir",
    "RidgeReadout",
    "nmse",
    "forecast",
    "classify",
]
