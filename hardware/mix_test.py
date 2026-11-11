"""Does the two-register switch mixing carry a nonlinear mixing?

Each packet carries TWO independent inputs: u in the IP ID low byte,
v in the IP TTL field. The switch multiplies the DELAYED versions
(u_prev * v_prev) into a mixing register and reports it.

Test: can a linear readout on the receipt [count, u_prev, v_prev, mix]
predict u[t]*v[t]? If the switch's mixing is real, the receipt's mix
field should carry it and NMSE should be low.
"""
import socket
import struct
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, "/root/binetic_pkg")
from binetic.core import _ridge_fit, _cv_lambda, nmse


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
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); s.bind(("veth_in", 0))
SRC=bytes.fromhex("020000000001"); DST=bytes.fromhex("020000000002")
for i in range(len(u)):
    qu = int(np.clip(u[i],0,1)*255)
    qv = int(np.clip(v[i],0,1)*255)
    payload = bytes(16)
    ip = struct.pack("!BBHHHBBH4s4s",0x45,0,20+len(payload),
                     (i & 0xFF00) | qu,     # id: carries u
                     0,
                     64 if qv==0 else qv,   # ttl: carries v
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
s.settimeout(15.0)
s.bind(("veth_out", 0))
while True:
    try:
        pkt, anc, _, _ = s.recvmsg(2048, 1024)
    except socket.timeout:
        break
    if len(pkt) < 50: continue
    receipt = pkt[34:50]
    if len(receipt) == 16:
        count, u_prev, v_prev, mix = struct.unpack("!IIII", receipt)
    else:
        count, u_prev, v_prev, mix = 0,0,0,0
    print(f"{count} {u_prev} {v_prev} {mix}", flush=True)
"""


def run(u, v):
    us = ",".join(f"{x:.6f}" for x in u)
    vs = ",".join(f"{x:.6f}" for x in v)
    rcv = subprocess.Popen(["ip", "netns", "exec", "ns_out", "python3", "-c",
                            RECEIVER], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, bufsize=1)
    time.sleep(0.7)
    subprocess.run(["ip", "netns", "exec", "ns_in", "python3", "-c",
                    SENDER, us, vs], capture_output=True, text=True, timeout=400)
    try:
        out, _ = rcv.communicate(timeout=25)
    except subprocess.TimeoutExpired:
        rcv.kill()
        out, _ = rcv.communicate()
    counts, uprevs, vprevs, mixes = [], [], [], []
    for line in out.strip().splitlines():
        p = line.split()
        if len(p) < 4: continue
        counts.append(int(p[0])); uprevs.append(int(p[1]))
        vprevs.append(int(p[2])); mixes.append(int(p[3]))
    return (np.array(counts), np.array(uprevs),
            np.array(vprevs), np.array(mixes))


def main():
    n = 200
    rng = np.random.default_rng(3)
    u = rng.uniform(0, 1, n)
    v = rng.uniform(0, 1, n)

    print("== two-register switch mixing ==")
    counts, up, vp, mix = run(u, v)
    m = min(up.size, u.size - 1)
    print(f"captured {m} receipts")
    if m < 40: return

    up, vp, mix = up[:m], vp[:m], mix[:m]
    uu, vv = u[:m] * 255, v[:m] * 255

    # does the receipt's u_prev/v_prev track the delayed inputs?
    print(f"\ncorr(u_prev[t], u[t-1]) = {np.corrcoef(up, np.roll(uu,1)[:m])[0,1]:.4f}")
    print(f"corr(v_prev[t], v[t-1]) = {np.corrcoef(vp, np.roll(vv,1)[:m])[0,1]:.4f}")

    # THE TEST: does the mix field actually equal u_prev * v_prev / 256?
    expected = (up * vp) >> 8
    err = np.abs(mix.astype(float) - expected).mean()
    print(f"\nmix field vs computed u_prev*v_prev>>8: mean abs err = {err:.2f}")
    exact = np.mean(mix == expected)
    print(f"exact agreement: {exact*100:.1f}%")

    # can a linear readout predict u[t]*v[t] from the receipt?
    X = np.stack([counts[:m].astype(float), up.astype(float),
                  vp.astype(float), mix.astype(float)], axis=1)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    tgt = uu * vv     # the genuine nonlinear mixing of current inputs
    cut = int(m * 0.7)
    lam = _cv_lambda(X, tgt[:m], cut)
    w = _ridge_fit(X[:cut], tgt[:cut], lam)
    print(f"\npredict u[t]*v[t] from receipt: NMSE = {nmse(tgt[cut:m], X[cut:]@w):.4f}")

    # baseline: predict from u_prev, v_prev alone (no mix register)
    Xb = np.stack([up.astype(float), vp.astype(float)], axis=1)
    Xb = (Xb - Xb.mean(0)) / (Xb.std(0) + 1e-9)
    w2 = _ridge_fit(Xb[:cut], tgt[:cut], 1e-3)
    print(f"baseline (u_prev,v_prev only): NMSE = {nmse(tgt[cut:m], Xb[cut:]@w2):.4f}")


if __name__ == "__main__":
    main()
