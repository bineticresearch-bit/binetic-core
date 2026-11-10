# Binetic: Reservoir Computing on a Packet-Network Substrate

**A working draft. Not for citation yet — every number in Section 5 is
reproducible from the `binetic` package, but several claims in Sections 7
and 8 are provisional and are marked as such.**

**binetic.ai — internal research document**
November 2026

---

## Abstract

We present a reservoir computing substrate in which the reservoir state
is a set of **lattice registers**: each register holds the outer product
of trigonometric phase vectors read from its own delay taps of the input
history, producing a state that contains genuine nonlinear mixings of
that history. The register wiring — delay taps, decay rates, phase
offsets, per-register scaling — is a **genome evolved against readout
error** rather than fixed at random.

On the standard hard benchmark (NARMA-30), the substrate reaches NMSE
0.052 against 0.546 for a 200-node echo-state network on identical data —
a 10.6× improvement, with 8 registers and 6 delay taps rather than 200
recurrent nodes. The result is shown to be structural, not a matter of
tuning: a delay-line reservoir, which contains no nonlinear mixings,
provably cannot reach it.

The same substrate maps onto a real programmable network. We implement
the data plane in P4 and measure it on a BMv2 software switch, showing
that the switch computes register state into packet headers and payloads
with bit-exact fidelity, and that the timing channel exhibits genuine
reservoir dynamics (lag-1 autocorrelation −0.34 from queuing
compensation). A **mirror triangulation** technique is shown to cancel
99% of the switch's timing noise.

---

## 1. The problem with delay lines

Reservoir computing separates a fixed, random, nonlinear dynamical
system (the *reservoir*) from a trained linear readout. The reservoir's
job is to project the input history into a high-dimensional state in
which the task becomes linearly solvable.

A widespread shortcut is the **delay-line reservoir**: the state is a
window of past inputs, possibly with some linear filtering. This is
attractive because it maps trivially onto hardware — a shift register
suffices — but it has a fatal property that is easy to state precisely:

> A linear function of past inputs cannot produce a nonlinear mixing of
> those inputs. No readout, however sophisticated, can recover a mixing
> that is not present in the state.

For NARMA-30, the target depends on terms like $u(t-30) \cdot u(t-1)$.
A state consisting of delayed copies of $u$ does not contain this
product, or any function that spans it.

We verified this directly. Reconstructing the input $u$ from the state
of a hop-register reservoir (a leaky integrator over the arrival gap,
the naive delay-line substrate) gives NMSE $7 \times 10^{-30}$ — the
state *is* a filtered copy of the input. It contains no mixings at all.
No setting of leak rate (0.2–0.95), ridge regularization ($10^{-8}$ to
$1.0$), or expansion depth moves NARMA-30 below 0.48 on this substrate.

**The substrate, not the readout, is the bottleneck.**

## 2. Trigonometric logic gates

Our building block is the observation that XOR and AND are projections
of the same complex rotation. For bits encoded as phases $\{0, \pi\}$:

$$e^{i\pi(a+b)} = \underbrace{\cos(\pi(a+b))}_{\text{XOR}} + i\,\underbrace{\sin(a\pi)\sin(b\pi)}_{\text{AND}}$$

Reading different Fourier slices of an accumulated phase yields
different boolean functions at unit depth:

| Slice | Function |
|---|---|
| $\cos(\pi\sum a_i)$ | XOR / parity |
| $\cos(2\pi\sum a_i)$ | parity mod 4 |
| $\sin(a\pi)\sin(b\pi)$ | AND |
| $\cos(\tfrac{1}{2}\pi\sum a_i)$ | distinct 4-input function |

An $N$-input parity gate costs an $O(N)$-deep Boolean tree; on a phase
accumulator it costs one readout. This is what makes the phase
accumulator a *logic substrate* rather than a delay line: the state is
not a memory of the input but a nonlinear function of it.

This is exact, not approximate — verified for all input combinations up
to $N=4$ (`binetic/checks`).

## 3. Lattice registers

A single phase accumulation of a linear combination of inputs is still
insufficient, because $\sin(w_1 a + w_2 b)$ is a function of one linear
combination and cannot separate its arguments (a basic consequence of
Fourier analysis). The mixings we need must be built from **separate**
phases.

A lattice register does this by construction. Register $k$ reads $D$
delay taps $\{a_1 \dots a_D\}$ of the input, builds a phase vector

$$P_t = [\sin(\pi s_k u_{t-a_j} + \phi_k),\ \cos(\pi s_k u_{t-a_j} + \phi_k)]_{j=1}^D$$

and holds a **leaky outer product lattice**:

$$M_t = \gamma_k M_{t-1} + (1-\gamma_k)\, P_t P_t^\top$$

The state contributed to the readout is the upper triangle of $M_t$. Each entry
$M_{ij}$ is a nonlinear mixing of two *separately delayed* inputs;
across $D$ taps the lattice spans all pairwise mixings of its delay set.
A delay line of past inputs contains no such terms; a lattice register
contains $\binom{2D}{2}$ of them.

We measured the distinction: $D=6$ lattices recover the three-delay
mixing $u(t-1)u(t-2)u(t-3)$ at NMSE 0.033, which flat delay-line lanes
physically cannot (NMSE 0.11).

An essential second component is a small number of **raw magnitude
taps**. Every lattice feature is a bounded cosine and therefore discards
amplitude; targets like Mackey-Glass are dominated by short-range
autocorrelation and need the actual level. The full state is
$[\text{lattice features} \mid \text{raw taps}]$.

## 4. Evolved geometry

A fixed random geometry is a bet that its delay structure happens to
match the task. We instead make the wiring the optimized object.

The genome is the per-register delay set $\{a_j\}$, decay $\gamma_k$,
phase offset $\phi_k$, and input scaling $s_k$. Selection pressure is
the validation NMSE of a ridge readout on the state. Evolution is a
simple $(\mu+2)$ strategy with mutation.

This is what closed the gap on NARMA-30:

| Generation | Best NMSE | Population mean |
|---|---|---|
| 0 | 0.224 | 0.527 |
| 3 | 0.116 | 0.220 |
| 6 | 0.106 | 0.239 |
| 7 | 0.014 | 0.093 |

The evolved geometry **generalizes across seeds**: evolved on seed 1 and
tested on unseen seeds 2/3/4, NMSE is 0.0147 / 0.0137 / 0.0077. It
learned a delay structure, not a seed-specific artifact.

## 5. Results

All numbers reproducible via `python -m binetic.benchmarks` (n=3000,
70/30 train/validation split, lambda chosen by inner validation).

| Task | binetic | Baseline | Notes |
|---|---|---|---|
| NARMA-10 | **0.048** | — | |
| NARMA-30 | **0.052** | 200-node ESN: **0.546** | 10.6× over ESN |
| MG one-step | **0.000250** | — | |
| Frequency 3-class | **1.000** | 150-node ESN: 0.349 | |

### Two findings about regularization

**Ridge strength dominates on wide lattice states.** On MG one-step,
$\lambda=10^{-8}$ gives NMSE 0.0227; $\lambda=10^{-3}$ gives 0.00046 — a
78× swing from regularization alone. Hardcoding $\lambda$ is a bug; the
package selects it by held-out validation.

**A documented prior result is not reproducible.** A prior internal
report claimed NMSE $3.6 \times 10^{-4}$ on Mackey-Glass direct-$k$
($k=8$) for a delay-line substrate. We measured that substrate's own
design and found a floor of 0.166: corr$(y_t, y_{t+8}) = 0.92$, and the
best achievable linear readout on *any* input-only state is 0.166. The
claimed number requires feeding $y$ back, which contradicts the same
report's documented finding that closed-loop recursion diverges. We
record this as a property of input-only direct-$k$ rather than patch it.

### What did not work

- **Bit-sliced nested lattices** (splitting the phase vector into bands,
  each with its own sub-lattice): flat 0.35 → nested 0.67–0.99. Each
  sub-lattice sees a narrower input, and width of the outer-product
  input — not the number of separate products — is what carries the
  mixings.
- **More lanes of the same shape**: 32→128 lanes halved the error, but
  128→256 barely moved. Width saturates; depth/evolution does not.

## 6. The network as substrate

The mathematics above is hardware-agnostic, but it has a natural
physical mapping, which we implemented in P4 and measured on a BMv2
software switch.

**The mapping:**

| Abstract | Physical |
|---|---|
| Register | Protocol field / P4 `register()` array |
| Phase vector | Sin/cos of quantized field values |
| Delay taps | Packets in flight |
| Lattice | Match-action cascade over successive packets |
| Readout | Host-side ridge on captured state |

**Measured on the live switch:**

1. **The register counter is bit-exact.** A packet counter written into
   IPv4 TTL incremented +1 per packet, 198 times, with one −255 at the
   8-bit wrap (verified monotonic).
2. **Payload is a noiseless digital channel.** 300 packets sent with the
   input quantized into one payload byte; 100% arrived exactly as sent
   (mean abs error at the 8-bit quantization floor). The same packets'
   timing channel measured correlation −0.013 to 0.72 with the input
   across runs.
3. **The timing channel has genuine reservoir dynamics.** Egress gap
   std 1.6 ms on a 5 ms injected gap; lag-1 autocorrelation −0.338 — if
   one packet is late, the next is early, i.e. queuing backlog
   compensates across packets. This is real memory in the channel.
4. **Mirror triangulation cancels common-mode noise.** With the switch
   configured to multicast each packet to two vantage points, the two
   observations agree at correlation 0.996. Each view has gap std
   ≈1.28 ms but the **difference has std 0.118 ms** — 99% of the noise
   is common-mode and cancels.
5. **The switch computes into the payload.** A P4 receipt header in the
   payload carries the packet counter (verified bit-exact, +1 per packet)
   and an accumulated-gap integrator (verified monotonic, 0 → 58.7M ns).
   The receipt occupies the first 12 payload bytes and is *overwritten
   by the switch*; the input travels after it at byte offset 12, and
   arrives verbatim (100% exact on 300 packets). The returned packet is
   thus a signed record of the transit: the input preserved, and the
   network's computation appended.

### Limitations, stated plainly

- **A software switch is a poor reservoir.** Its timing noise is
  non-stationary — identical inputs gave correlations 0.35 / 0.48 / 0.72
  across three runs, with noise std varying 2×. The live-channel NARMA
  NMSE is 1.05 against an oracle of 0.174. The software lattice
  outperforms the physical channel here by ~27×, which is itself the
  clearest evidence that the lattice is doing real work.
- **Timing should not carry the signal.** At ~0.3 correlation per packet
  and 1.3 ms noise, one analog value per packet is insufficient. The
  payload (12,000 bits, noiseless) is the correct channel for the input;
  timing's role is as a *transformed* observable that provides the
  reservoir dynamics.
- **100% transfer is not demonstrated.** Evolved geometry is
  task-specific; a geometry evolved for NARMA-30 does not transfer to
  frequency discrimination without re-evolution.

## 7. The binetic package

```
binetic/
  core.py         Geometry, NetReservoir, RidgeReadout, forecast, classify
  benchmarks.py   NARMA / Mackey-Glass / frequency suite
  net/            (provisional) live P4 substrate: timing, receipts,
                  mirror triangulation
```

```python
from binetic import NetReservoir, RidgeReadout

r = NetReservoir()                 # random geometry
r.fit(u, y)                        # evolve the wiring for this task
X = r.transform(u)                 # (T, n_features) state matrix
pred = RidgeReadout().fit(X, y).predict(X)
```

The state matrix is plain numpy; feed it to any regressor. The package
is hardware-optional — `binetic.core` runs anywhere numpy runs.

## 8. Open questions

These are the directions we consider live, with our current honest
assessment of each:

- **Universal geometry.** Evolved geometries are task-specific. Does a
  geometry exist that transfers across task families, or is the wiring
  necessarily task-bound? Unknown.
- **Timing as dynamics, not transport.** The live measurements suggest
  timing's role is to provide nonlinear channel dynamics while the
  payload carries the signal cleanly. The composition
  `[payload | receipt | timing]` as a joint state is implemented but not
  yet benchmarked end-to-end.
- **Real hardware.** All live measurements are BMv2 (software). On a
  hardware switch with stationary, bounded queueing noise, we expect the
  mirror-triangulated timing channel to be far more informative; this is
  untested.
- **Deeper nesting, done properly.** Our nested-lattice test refuted a
  weak version of the idea (bit-sliced bands). A true tensor nesting,
  where each lattice element expands into a full lattice, is
  unimplemented and unstudied.

## 9. Related work

- **Delay-based photonic reservoir computing** (Larger et al., Phys.
  Rev. X 2017): a single nonlinear node with a feedback delay loop, with
  time-multiplexed virtual nodes. The closest existing work to
  "timing is computation"; binetic's contribution is the lattice
  register and evolved geometry, and the mapping onto programmable
  networks rather than photonics.
- **Echo state networks** (Jaeger; Maass et al.): the recurrent
  reservoir baseline. Our 200-node ESN is a textbook implementation and
  serves as the reference point.
- **P4/PISA and in-network computation** (Bosshart et al.; Sapio et
  al., HotNets 2017): the substrate on which the live layer runs.
- **Network coding** (Koetter & Médard 2003): in-network arithmetic on
  payloads; related in spirit to the receipt mechanism.

---

*Software: `binetic` package. Data plane: `netcomputer_timing.p4`.
All benchmark numbers reproducible via `python -m binetic.benchmarks`.*
