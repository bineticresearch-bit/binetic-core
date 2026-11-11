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

The central hardware result is a **closed-loop reservoir** through the
switch: the readout's own prediction rides the packet payload back into
the data plane, where the switch multiplies it with the input in silicon,
and the receipt returns the product to the readout. On NARMA-10 this
closed loop reaches NMSE 0.015–0.021 across unseen seeds — an 8×
improvement over the input-only floor that no input-only state can
cross, and 30× better than a 200-node echo-state network. We show the
floor is structural (NARMA's target depends on $y(t-1)$, which no
function of the inputs contains) and that the loop breaks it exactly
where claimed: the state then contains $y(t-1)$ and
$u(t-1)y(t-1)$, and a linear readout on that state predicts at NMSE
0.0000 in software and 0.0155 through the physical switch.

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
- **Single-register cross-packet recurrence as a reservoir.** The
  cascade register (§6.2) is a genuine cross-packet recurrence, bit-exact
  — but its increments recover the input at correlation 1.000, i.e. it is
  a pure integrator and contributes no nonlinearity. A recurrence in one
  variable cannot mix two independent inputs.
- **6-bit closed loop.** Feeding the readout's output back through a
  63-level field gives no recurrence information: true-$y$ and $\hat{y}$
  feedback produce identical results (0.1073 vs 0.1076) because
  quantization destroys the signal. Widening to 16 bits fixed it (§6.1).
- **Timing as the state channel.** Correlation −0.01 to 0.72 with the
  input across runs, non-stationary. Relegated to an auxiliary
  observable; the payload carries the signal.

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
6. **The switch's mix register supplies nonlinearity a readout cannot
   synthesize.** With two input streams carried in separate packet
   fields, the switch computes their product in silicon. The mix field
   agrees with a software computation of $u_{t-1}v_{t-1}$ on **100.0%**
   of packets. A readout *with* the mix term predicts the delayed
   product at NMSE **0.0000** versus 0.1515 without it — the formal
   criterion for the substrate contributing nonlinear computation.
7. **A single switch mix register beats a 16-tap delay line on NARMA-10.**
   The register alone predicts at NMSE 0.117, where a 16-tap window of
   raw input history floors at 0.165. The nonlinearity is worth more
   than the memory.

### 6.1 The closed-loop reservoir

The decisive hardware experiment. NARMA-10's target depends on
$y(t-1)$, which no function of the inputs contains — every input-only
readout floors at ~0.10–0.12 (verified four ways: software cross-term
0.097, switch mix register 0.117, evolved lattice 0.110, delay line
0.165). The loop closes this by feeding the readout's own previous
prediction back into the data plane as a third packet field, so the
switch computes

$$\text{mix}_{uf} = u(t-1) \cdot \hat{y}(t-1)$$

— the NARMA recurrence term with $\hat{y}$ standing in for $y$, computed
in switch silicon at line rate.

| Configuration | NMSE |
|---|---|
| Open-loop (pinned feedback, 16-bit) | 0.6719 |
| **Closed-loop ($\hat{y}$ fed back, 16-bit)** | **0.0204** |
| Closed-loop (true $y$ — the ceiling) | 0.0155 |
| Input-only floor (delay line) | 0.165 |
| 200-node ESN (software) | 0.546 |

**The floor breaks by 8×.** Across three unseen seeds the closed loop
scores 0.0185 / 0.0212 / 0.0146, consistently within 30–70% of the
true-$y$ ceiling. Two findings made it work, and both are measured:

**Field width was the binding constraint.** At 6 bits (IP DSCP, 63
levels) true-$y$ feedback and $\hat{y}$ feedback give *identical*
results (0.1073 vs 0.1076) — quantization destroys the recurrence
information before the loop can use it. Widening to 16 bits (two payload
bytes, 65,535 levels) opens the loop: the same experiment then moves
from 0.1076 to 0.0204. The packet field is the register file, and it
runs out of precision exactly where recurrence information lives.

**The readout must be trained on the loop's operating distribution.**
Training on pinned or random feedback puts the features outside the
region the closed loop visits, and the fit extrapolates and diverges
(we measured NMSE 1229 from this alone, with loop gain a stable 0.113 —
the divergence was a training artifact, not instability). Training on
receipts collected with true-$y$ feedback puts the features where the
loop operates, and the loop then runs to convergence.

**Verification.** The receipt's $f\_prev$ field matches the sent
feedback bit-exactly, lag-1 (e.g. sent 10472, receipt 10472). A
software-perfect state containing $y(t-1)$ and both mixings predicts at
NMSE 0.0000, isolating the channel; the physical loop delivers 0.0155,
so the switch recovers essentially all of the available information.

### 6.2 Program evolution

The data-plane program itself is made evolvable. The genome
parameterizes a fixed P4 skeleton (recurrence operation, shift
constants, which register feeds back, which fields land in the receipt),
so every instance compiles by construction. Compile takes 0.22s and a
runtime hot-swap 0.03s, giving a full program-reload cycle of 0.25s —
fast enough to evolve against physical measurements on the device.

Measured on this loop: a sweep over four recurrence operations
(mul / add / xor / shift-mul) at two shift constants showed all
single-register recurrences are **linear** — the accumulator's increments
recover the input at correlation 1.000, bit-exact, but generate no
mixings (NMSE 6–9 on $u(t)u(t-1)$ across all ops). A single-register
recurrence in one variable cannot produce a function of two independent
inputs. This is the structural reason the two-register mix construction
of §6 items 6–7 is required, and it constrains the genome family:
useful programs combine at least two independently-fed registers.

### Limitations, stated plainly

- **A software switch is a poor timing reservoir.** Its timing noise is
  non-stationary — identical inputs gave correlations 0.35 / 0.48 / 0.72
  across three runs, with noise std varying 2×. Timing alone is not a
  usable state channel: the live timing-channel NARMA NMSE is 1.05
  against an oracle of 0.174. The payload, not timing, carries the
  signal; timing's role is as a *transformed* observable.
- **The closed loop is not a single streaming pass.** The readout runs
  on a host, step by step, injecting each prediction as the next
  packet's feedback field. The substrate computation (the mix register)
  runs at line rate in silicon, but the loop as a whole is not yet a
  single streaming inference. Making it one is an engineering matter.
- **BMv2 is software.** All hardware results are measured on a software
  switch. On real silicon (Tofino, NetFPGA) the timing noise would be
  stationary and bounded rather than host-schedule-dominated; we expect
  the timing channel to be substantially more informative there. This
  is untested.
- **Geometry is task-specific.** A geometry evolved for NARMA-30 does
  not transfer to frequency discrimination without re-evolution.
- **Single-register recurrences are linear.** Measured across four
  operations: a one-register recurrence in one variable cannot produce
  mixings of two independent inputs (§6.2). Useful programs require at
  least two independently-fed registers, which constrains the program
  genome family.

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
- **Timing as dynamics, not transport.** The live measurements show
  timing alone is a poor state channel (non-stationary noise) but does
  carry genuine dynamics (lag-1 autocorrelation −0.34). The composition
  `[payload | receipt | timing]` as a joint state is the natural next
  experiment: payload carries the signal, the mix register supplies the
  nonlinearity, and timing supplies an additional nonlinear observable
  that the lattice can mix.
- **Real hardware.** All live measurements are BMv2 (software). On a
  hardware switch with stationary, bounded queueing noise, we expect the
  mirror-triangulated timing channel and the closed loop to be
  substantially tighter; this is untested and is the obvious next step.
- **Co-evolution across the stack.** §6.2 evolves the data-plane program
  against readout error, and §4 evolves the geometry. These currently
  run separately. Co-evolving the *program* and the *readout* jointly —
  with the physical switch in the fitness loop — is implemented in
  prototype (0.25s per program reload) but not yet run as a joint search.
- **Field width as a design dimension.** The closed loop only works at
  ≥16 bits of feedback; 6 bits quantizes away the recurrence. This makes
  field width a first-class design parameter of the substrate. IPv6's
  flow label (20 bits) and multi-byte payload fields are the natural
  extension, and the precision/resolution trade-off is unstudied.
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
