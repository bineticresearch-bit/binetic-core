"""binetic.net — run the substrate through a real P4 switch. PROVISIONAL.

This layer is a working prototype on BMv2. It is not part of the
benchmarked core: every number in the whitepaper's results table comes
from binetic.core, which runs anywhere numpy runs.

The network provides three observable channels per packet, with very
different measured fidelities:

  payload  — a noiseless digital channel (measured: 100% exact, error at
             the 8-bit quantization floor, correlation 1.000 with the
             input). This is where the input belongs.
  receipt  — register state the switch writes INTO the payload (packet
             counter verified bit-exact +1 per packet, gap integrator
             verified monotonic). The returned packet is a signed record
             of the transit.
  timing   — a noisy analog channel, correlation -0.01 to 0.72 with the
             input across runs, non-stationary. Its value is not
             transport but dynamics: the queueing transform is the
             reservoir's nonlinearity.

Frame layout convention:

    eth (14) | IPv4 (20) | receipt (12, switch-written) | input payload

The switch OVERWRITES the first 12 payload bytes with the receipt
(hop_count, gap_ns, accum_ns as three big-endian uint32). Input must be
placed at byte offset 12 or later to survive. LiveChannel implements
this convention.
"""

from .channel import LiveChannel, ChannelResult

__all__ = ["LiveChannel", "ChannelResult"]
