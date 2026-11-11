# Hardware experiments — live P4 substrate

These are the experiments behind the whitepaper's §6 results. Each one
runs against a real BMv2 switch through network namespaces; none are
simulations.

## Layout

| File | What it demonstrates |
|---|---|
| `netcomputer_mix.p4` | Two-register mixing: `mix = u_prev * v_prev` in switch silicon |
| `netcomputer_clf.p4` | Closed-loop data plane: 16-bit feedback in the payload |
| `netcomputer_cascade.p4` | Cross-packet recurrence (a pure integrator — the negative result) |
| `mix_test.py` | Mix register is bit-exact; readout with it NMSE 0.0000 vs 0.1515 without |
| `narma_switch.py` | NARMA-10 end-to-end: mix register 0.117 vs 16-tap delay line 0.165 |
| `clf16.py` | **The closed loop**: 0.0204 NMSE, breaking the input-only floor by 8× |
| `program_genome.py` | Program evolution: 0.25s program-reload cycle on the live switch |
| `verify_wire.py`, `verify_streams2.py` | Channel verification primitives |

## Topology

```
ns_in (sender) ──veth──┐
                       ├── simple_switch (netcomputer_*.p4)
ns_out (receiver) ──veth─┘
```

Layer-2 injection (AF_PACKET) so the frame layout is exact; nanosecond
capture timestamps via SO_TIMESTAMPNS control messages.

## Frame layout (netcomputer_clf.p4)

```
eth (14) | IPv4 (20) | receipt (20, switch-written) | payload (4)
                                                        fb_wide (2B, 16-bit)
                                                        u_byte  (1B)
                                                        d_byte  (1B)
```

The receipt is *overwritten by the switch*, so input data must be placed
after the 20-byte receipt region.

## Reproduce the closed loop

Requires p4c, simple_switch, and root.

```bash
p4c --target bmv2 --arch v1model --std p4-16 netcomputer_clf.p4 \
    -o out/clf.json/netcomputer_clf.json
simple_switch --thrift-port 9092 --enable-swap \
    -i 0@veth_in_br -i 1@veth_out_br -- out/.../netcomputer_clf.json
python3 clf16.py
```
