"""Closed-loop NARMA with a 16-BIT feedback channel.

The 6-bit DSCP channel was shown to be the bottleneck: feeding back TRUE
y gave the same NMSE as feeding back y_hat (0.1073 vs 0.1076), so the
channel couldn't carry recurrence information at 63 levels.

Now the feedback rides two payload bytes: 65535 levels instead of 63.
y in [0.2, 1.3] maps to ~50,000 distinct values. If the y-recurrence
floor is a precision artifact, this breaks it. If it holds at 16 bits,
the floor is structural.
"""
import sys

import numpy as np

sys.path.insert(0, ".")
from clf_narma import narma

SENDER = r'''
import socket, struct, sys, time
import numpy as np
def csum(d):
    if len(d) % 2: d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d)//2), d)); s=(s>>16)+(s&0xFFFF); s+=(s>>16)
    return (~s)&0xFFFF
u = np.array(sys.argv[1].split(","), dtype=float)
d = np.array(sys.argv[2].split(","), dtype=float)
fb = np.array(sys.argv[3].split(","), dtype=float)      # 16-bit feedback
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); s.bind(("veth_in", 0))
SRC=bytes.fromhex("020000000001"); DST=bytes.fromhex("020000000002")
for i in range(len(u)):
    qu = int(np.clip(u[i],0,1)*255)
    qd = int(np.clip(d[i],0,1)*255)
    qf = int(np.clip(fb[i],0,1)*65535)                  # 16-bit
    # payload: receipt(20, switch-written) + fb(2) + u(1) + d(1)
    payload = bytes(20) + struct.pack("!HBB", qf, qu, qd)
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20+len(payload),
                     qd, 0, 64, 253, 0,                 # ID: carries d
                     socket.inet_aton("10.0.1.1"), socket.inet_aton("10.0.1.2"))
    ip = ip[:10]+struct.pack("!H",csum(ip))+ip[12:]
    s.send(DST+SRC+struct.pack("!H",0x0800)+ip+payload)
    if i < len(u)-1: time.sleep(0.002)
sys.stderr.write(f"sent {len(u)}\n")
'''

RECEIVER = r'''
import socket, struct, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.setsockopt(socket.SOL_SOCKET, 35, 1)
s.settimeout(20.0)
s.bind(("veth_out", 0))
while True:
    try:
        pkt, anc, _, _ = s.recvmsg(2048, 1024)
    except socket.timeout:
        break
    if len(pkt) < 54: continue
    count, u_p, f_p, m_uf, m_ud = struct.unpack("!IIIII", pkt[34:54])
    print(f"{count} {u_p} {f_p} {m_uf} {m_ud}", flush=True)
'''


def collect(u, d, fbvals, timeout=60):
    import subprocess, time
    us = ",".join(f"{x:.6f}" for x in u)
    ds = ",".join(f"{x:.6f}" for x in d)
    fs = ",".join(f"{x:.6f}" for x in fbvals)
    rcv = subprocess.Popen(["ip", "netns", "exec", "ns_out", "python3", "-c",
                            RECEIVER], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True, bufsize=1)
    time.sleep(0.8)
    subprocess.run(["ip", "netns", "exec", "ns_in", "python3", "-c",
                    SENDER, us, ds, fs], capture_output=True, text=True,
                   timeout=timeout)
    try:
        out, _ = rcv.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        rcv.kill()
        out, _ = rcv.communicate()
    rows = []
    for line in out.strip().splitlines():
        p = line.split()
        if len(p) >= 5:
            rows.append([float(x) for x in p[:5]])
    return np.array(rows)


def score(R, y, w, cut):
    m = R.shape[0]
    feats = np.stack([R[:m, 1], R[:m, 2], R[:m, 3], R[:m, 4]], axis=1)
    X = np.hstack([np.ones((m, 1)), feats])
    yh = X @ w
    return float(np.mean((y[:m][cut:] - yh[cut:]) ** 2) / np.var(y[:m][cut:]))


def main():
    n = 400
    train_seeds = (1, 2, 3)
    test_seed = 7

    # ---- training receipts with VARYING wide feedback ----
    Xs, ys = [], []
    for seed in train_seeds:
        u, y = narma(n, 10, seed=seed)
        d = np.concatenate([[u[0]] * 9, u[:-9]])
        rng = np.random.default_rng(seed + 50)
        fb = np.clip(np.cumsum(rng.standard_normal(n) * 0.02) + 0.5, 0.02, 0.98)
        R = collect(u, d, fb)
        m = R.shape[0]
        if m < 60:
            print(f"  seed {seed}: only {m} receipts"); continue
        feats = np.stack([R[:m, 1], R[:m, 2], R[:m, 3], R[:m, 4]], axis=1)
        Xs.append(np.hstack([np.ones((m, 1)), feats]))
        ys.append(y[:m])
    X = np.vstack(Xs)
    ytr = np.concatenate(ys)
    cut = int(X.shape[0] * 0.7)
    w = np.linalg.lstsq(X[:cut], ytr[:cut], rcond=None)[0]
    print(f"trained on {X.shape[0]} receipts, 16-bit feedback channel")

    u, yt = narma(n, 10, seed=test_seed)
    d = np.concatenate([[u[0]] * 9, u[:-9]])
    cutn = int((n - 1) * 0.7)

    # open loop: pinned feedback
    R = collect(u, d, np.full(n, 0.5))
    open_nmse = score(R, yt, w, cutn)
    print(f"open-loop (pinned feedback):  NMSE = {open_nmse:.4f}")

    # closed loop: y_hat fed back at 16 bits
    fb = np.full(n, 0.5)
    y_hat = np.zeros(n)
    for t in range(n - 1):
        u_prev = u[t] * 255.0
        f_prev = fb[t] * 65535.0
        d_prev = d[t] * 255.0
        mix_uf = (u_prev * f_prev) / 65536.0
        mix_ud = (u_prev * d_prev) / 256.0
        feat = np.array([1.0, u_prev, f_prev, mix_uf, mix_ud])
        y_hat[t] = float(feat @ w)
        fb[t + 1] = max(0.0, min(1.0, (y_hat[t] - 0.1) / 1.5))
    R2 = collect(u, d, fb)
    closed = score(R2, yt, w, cutn)
    print(f"closed-loop (y_hat, 16-bit):   NMSE = {closed:.4f}")

    # ablation: TRUE y fed back at 16 bits
    fb_true = np.clip((yt - 0.1) / 1.5, 0, 1)
    R3 = collect(u, d, fb_true)
    closed_true = score(R3, yt, w, cutn)
    print(f"closed-loop (TRUE y, 16-bit):  NMSE = {closed_true:.4f}")

    print("\n== verdict ==")
    print(f"6-bit reference: open 0.1154  closed 0.1076  true-y 0.1073")
    print(f"16-bit:          open {open_nmse:.4f}  closed {closed:.4f}  "
          f"true-y {closed_true:.4f}")


if __name__ == "__main__":
    main()
