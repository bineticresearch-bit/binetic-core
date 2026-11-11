"""NARMA end-to-end through the switch, mix register supplying the
nonlinearity, payload carrying the input, readout trained on the receipt.

Construction:
  u[t] = the NARMA input series
  v[t] = u[t-9]  (a delayed view of the same stream, set by the SENDER)
  -> the switch computes mix = u[t-1] * v[t-1] = u[t-1] * u[t-10]
     which is EXACTLY the NARMA-10 cross-term 1.5*u(t-10)*u(t-1)

So the receipt at packet t carries the NARMA cross-term computed in
switch silicon. The readout sees [count, u_prev, v_prev, mix] plus the
payload echo of u.

The remaining NARMA dependence is y[t-1], which no input-only state can
contain (measured earlier). So we compare against a software oracle that
gets the same input-only information, and the honest question is:

  can the switch-computed mixing CLOSE the gap between a raw delay line
  and the software lattice?

That is the exact criterion for the substrate contributing nonlinearity.
"""
import socket
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, "/root/binetic_pkg")
from binetic.core import _ridge_fit, _cv_lambda, nmse


def narma(n, order=10, seed=1):
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 0.5, size=n + order)
    y = np.zeros(n + order)
    y[:order] = 0.1
    for t in range(order, n + order):
        y[t] = (0.3 * y[t-1] + y[t-1] / (1.0 + y[t-1]**2)
                + 1.5 * u[t-order] * u[t-1]
                + 0.001 * np.sum(np.abs(y[t-order:t])) + 0.1)
    return u[order:], y[order:]


def csum(d):
    if len(d) % 2:
        d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d) // 2), d))
    s = (s >> 16) + (s & 0xFFFF)
    s += (s >> 16)
    return (~s) & 0xFFFF


SENDER = r"""
import socket, struct, sys, time
import numpy as np
def csum(d):
    if len(d) % 2: d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d)//2), d)); s=(s>>16)+(s&0xFFFF); s+=(s>>16)
    return (~s)&0xFFFF
u = np.array(sys.argv[1].split(","), dtype=float)
v = np.array(sys.argv[2].split(","), dtype=float)
payload_vals = np.array(sys.argv[3].split(","), dtype=float)
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); s.bind(("veth_in", 0))
SRC=bytes.fromhex("020000000001"); DST=bytes.fromhex("020000000002")
for i in range(len(u)):
    qu = int(np.clip(u[i],0,1)*255)
    qv = int(np.clip(v[i],0,1)*255)
    qp = int(np.clip(payload_vals[i],0,1)*255)
    payload = bytes(16) + bytes([qp])      # input after the receipt
    ip = struct.pack("!BBHHHBBH4s4s",0x45,0,20+len(payload),
                     (i & 0xFF00) | qu,    # id: carries u[t]
                     0,
                     64 if qv==0 else qv,  # ttl: carries v[t]
                     253,0,socket.inet_aton("10.0.1.1"),socket.inet_aton("10.0.1.2"))
    ip = ip[:10]+struct.pack("!H",csum(ip))+ip[12:]
    s.send(DST+SRC+struct.pack("!H",0x0800)+ip+payload)
    if i < len(u)-1: time.sleep(0.003)
sys.stderr.write(f"sent {len(u)}\n")
"""

RECEIVER = r"""
import socket, struct, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.setsockopt(socket.SOL_SOCKET, 35, 1)
s.settimeout(25.0)
s.bind(("veth_out", 0))
while True:
    try:
        pkt, anc, _, _ = s.recvmsg(2048, 1024)
    except socket.timeout:
        break
    if len(pkt) < 50: continue
    receipt = pkt[34:50]
    count, u_prev, v_prev, mix = (0,0,0,0)
    if len(receipt) == 16:
        count, u_prev, v_prev, mix = struct.unpack("!IIII", receipt)
    sec, ns = 0, 0
    for lvl, typ, data in anc:
        if typ == 35 and len(data) >= 16:
            sec, ns = struct.unpack("<qq", data[:16])
    print(f"{sec}.{ns:09d} {count} {u_prev} {v_prev} {mix}", flush=True)
"""


def run_through_switch(u, v, payload_vals, timeout=40):
    us = ",".join(f"{x:.6f}" for x in u)
    vs = ",".join(f"{x:.6f}" for x in v)
    ps = ",".join(f"{x:.6f}" for x in payload_vals)
    rcv = subprocess.Popen(["ip", "netns", "exec", "ns_out", "python3", "-c",
                            RECEIVER], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, bufsize=1)
    time.sleep(0.8)
    subprocess.run(["ip", "netns", "exec", "ns_in", "python3", "-c",
                    SENDER, us, vs, ps], capture_output=True, text=True,
                   timeout=timeout)
    try:
        out, _ = rcv.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        rcv.kill()
        out, _ = rcv.communicate()

    ts, counts, up, vp, mix = [], [], [], [], []
    for line in out.strip().splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        sec, ns = p[0].split(".")
        ts.append(float(sec) + float(ns) / 1e9)
        counts.append(int(p[1])); up.append(int(p[2]))
        vp.append(int(p[3])); mix.append(int(p[4]))
    order = np.argsort(ts)
    return (np.diff(np.array(ts)[order]),
            np.array(counts)[order], np.array(up)[order],
            np.array(vp)[order], np.array(mix)[order])


def fit_score(X, y, cut_frac=0.7):
    cut = int(len(X) * cut_frac)
    lam = _cv_lambda(X, y, cut)
    w = _ridge_fit(X[:cut], y[:cut], lam)
    return nmse(y[cut:], X[cut:] @ w)


def main():
    n = 400
    u, y = narma(n, 10, seed=1)

    # v[t] = u[t-9] so the switch's mix = u[t-1]*u[t-10] (the NARMA term)
    v = np.concatenate([[u[0]] * 9, u[:-9]])

    print("== NARMA end-to-end through the switch ==")
    gaps, counts, up, vp, mix = run_through_switch(u, v, u)
    m = min(up.size, u.size - 1)
    print(f"captured {m} receipts")
    if m < 60:
        print("insufficient capture")
        return

    gap_ms = gaps[:m] * 1000.0
    up, vp, mix = up[:m], vp[:m], mix[:m]
    uu = u[:m] * 255.0
    yy = y[:m]

    # verify the switch's mix register is the true NARMA cross-term
    truth = (up * vp) >> 8
    exact = float(np.mean(mix == truth))
    print(f"\nmix register == u[t-1]*u[t-10] (hardware) : exact {exact*100:.1f}%")

    # state matrices
    def std(M):
        return (M - M.mean(0)) / (M.std(0) + 1e-9)

    # 1) receipt only, WITH the mix register
    X_mix = std(np.stack([counts[:m].astype(float), up.astype(float),
                          vp.astype(float), mix.astype(float)], axis=1))
    # 2) receipt WITHOUT the mix register
    X_raw = std(np.stack([counts[:m].astype(float), up.astype(float),
                          vp.astype(float)], axis=1))

    print(f"\n{'readout state':<44}{'NMSE':>9}")
    print("-" * 55)
    s1 = fit_score(X_mix, yy)
    print(f"{'receipt with mix register':<44}{s1:>9.4f}")
    s2 = fit_score(X_raw, yy)
    print(f"{'receipt without mix register':<44}{s2:>9.4f}")

    # 3) software oracle: a delay line of raw inputs (same info, in software)
    taps = np.stack([np.roll(uu, i) for i in range(16)], axis=1)[:m]
    X_sw = std(taps)
    s3 = fit_score(X_sw, yy)
    print(f"{'software delay line (16 taps, no mix)':<44}{s3:>9.4f}")

    # 4) software delay line WITH the mixing terms supplied
    taps16 = np.stack([np.roll(uu, i) for i in range(16)], axis=1)[:m]
    cross = np.stack([np.roll(uu, i) * np.roll(uu, j)
                      for i, j in ((0, 1), (1, 9), (0, 9))], axis=1)[:m]
    X_sw2 = std(np.hstack([taps16, cross]))
    s4 = fit_score(X_sw2, yy)
    print(f"{'software delay line + cross products':<44}{s4:>9.4f}")

    print(f"\n== verdict ==")
    print(f"switch mix vs switch raw:        {s2:.4f} -> {s1:.4f}")
    print(f"  (the mix register closes the gap by {s2-s1:.4f} NMSE)")
    print(f"switch with mix vs software oracle: {s1:.4f} vs {s3:.4f}")
    print(f"switch with mix vs software+mix:    {s1:.4f} vs {s4:.4f}")


if __name__ == "__main__":
    main()
