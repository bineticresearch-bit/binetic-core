"""binetic.net.channel — the live P4 substrate. PROVISIONAL.

A thin orchestrator over the BMv2 switch: inject an input series encoded
as packet inter-departure timing, capture egress timing and payload
receipts, and return all three channels as numpy arrays.

Requires a running switch and root-level network namespaces. This module
does graceful degradation: if the switch or namespaces are unavailable
it raises a clear error rather than degrading silently, because a silent
fallback to simulation would make measured numbers meaningless.
"""

import socket
import struct
import subprocess
import time

import numpy as np

__all__ = ["LiveChannel", "ChannelResult"]


class ChannelResult:
    """The three channels from one live run.

    timing_ms : (T-1,) egress inter-arrival gaps in ms. Noisy analog.
    payload   : (T,) the payload byte as received. Noiseless digital.
    receipts  : (T, 3) hop_count, gap_ns, accum_ns the switch computed.
    """

    def __init__(self, timing_ms, payload, receipts):
        self.timing_ms = timing_ms
        self.payload = payload
        self.receipts = receipts

    def state_matrix(self, use_timing=True, use_payload=True,
                     use_receipts=True):
        """Combine the channels into one state matrix for a readout."""
        cols = []
        if use_payload:
            cols.append(self.payload[:, None] / 255.0)
        if use_receipts:
            r = np.asarray(self.receipts, dtype=float)
            # scale each receipt column independently to unit std
            r = (r - r.mean(axis=0)) / (r.std(axis=0) + 1e-12)
            cols.append(r)
        if use_timing:
            t = np.asarray(self.timing_ms, dtype=float)
            t = (t - t.mean()) / (t.std() + 1e-12)
            cols.append(t[:, None])
        return np.hstack(cols)


_SENDER = r"""
import socket, struct, sys, time
import numpy as np
def csum(d):
    if len(d) % 2: d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d)//2), d)); s=(s>>16)+(s&0xFFFF); s+=(s>>16)
    return (~s)&0xFFFF
u = np.array(sys.argv[1].split(","), dtype=float)
base = float(sys.argv[2]); scale = float(sys.argv[3])
gap_s = float(sys.argv[4])
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); s.bind(("veth_in", 0))
SRC = bytes.fromhex("020000000001"); DST = bytes.fromhex("020000000002")
for i, ut in enumerate(u):
    q = int(np.clip(ut, 0, 1) * 255)
    # Layout: receipt (12B, switch overwrites) | input byte | padding
    payload = bytes(12) + bytes([q]) + bytes(16)
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20+len(payload), i&0xFFFF, 0,
                     64, 253, 0, socket.inet_aton("10.0.1.1"),
                     socket.inet_aton("10.0.1.2"))
    ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
    s.send(DST + SRC + struct.pack("!H", 0x0800) + ip + payload)
    if i < len(u) - 1:
        time.sleep(gap_s if gap_s > 0 else (base + scale*ut)/1000.0)
sys.stderr.write(f"sent {len(u)}\n")
"""

_RECEIVER = r"""
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
    if len(pkt) < 34: continue
    # Frame layout:
    #   eth (14) | ip (20) | receipt (12, switch-written) | input payload
    # The switch OVERWRITES the first 12 payload bytes with the receipt.
    # So our input byte lives at offset 14+20+12 = 46.
    receipt = pkt[34:46]
    if len(receipt) == 12:
        hop, gap, accum = struct.unpack("!III", receipt)
    else:
        hop, gap, accum = 0, 0, 0
    payload_byte = pkt[46] if len(pkt) > 46 else 0
    sec, ns = 0, 0
    for lvl, typ, data in anc:
        if typ == 35 and len(data) >= 16:
            sec, ns = struct.unpack("<qq", data[:16])
    print(f"{sec}.{ns:09d} {payload_byte} {hop} {gap} {accum}", flush=True)
"""


def _run_ns(ns, code, args=(), timeout=60):
    return subprocess.Popen(["ip", "netns", "exec", ns, "python3", "-c", code]
                            + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, bufsize=1)


class LiveChannel:
    """Inject an input series through a running BMv2 switch and capture
    the three channels (timing, payload, receipts).

    Prerequisites (not managed by this class):
      - simple_switch running with the compiled netcomputer_timing.p4
      - namespaces ns_in and ns_out on the veth pairs

    Parameters
    ----------
    base_ms, scale_ms : encoding for variable-gap injection
    fixed_gap_s : if > 0, inject at a constant gap instead (timing only)
    """

    def __init__(self, base_ms=5.0, scale_ms=3.0, fixed_gap_s=0.0):
        self.base_ms = base_ms
        self.scale_ms = scale_ms
        self.fixed_gap_s = fixed_gap_s

    def check(self):
        """Verify the switch and namespaces exist. Raises RuntimeError."""
        r = subprocess.run("pgrep -f 'simple_switch --thrift-port'",
                           shell=True, capture_output=True, text=True)
        if not r.stdout.strip():
            raise RuntimeError(
                "no simple_switch running. Start it with the compiled "
                "netcomputer_timing.p4 before using LiveChannel.")
        r = subprocess.run("ip netns list", shell=True,
                           capture_output=True, text=True)
        for ns in ("ns_in", "ns_out"):
            if ns not in r.stdout:
                raise RuntimeError(f"network namespace {ns!r} missing")
        return True

    def run(self, u, timeout=60):
        """Inject u and return a ChannelResult."""
        u = np.asarray(u, dtype=float)
        self.check()

        rcv = _run_ns("ns_out", _RECEIVER)
        time.sleep(0.7)
        us = ",".join(f"{v:.6f}" for v in u)
        subprocess.run(["ip", "netns", "exec", "ns_in", "python3", "-c",
                        _SENDER, us, str(self.base_ms), str(self.scale_ms),
                        str(self.fixed_gap_s)],
                       capture_output=True, text=True, timeout=timeout)
        try:
            out, _ = rcv.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            rcv.kill()
            out, _ = rcv.communicate()

        ts, payload, hops, gaps, accums = [], [], [], [], []
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            sec, ns = parts[0].split(".")
            ts.append(float(sec) + float(ns) / 1e9)
            payload.append(int(parts[1]))
            hops.append(int(parts[2]))
            gaps.append(int(parts[3]))
            accums.append(int(parts[4]))
        order = np.argsort(np.array(ts))
        ts = np.array(ts)[order]
        timing = np.diff(ts) * 1000.0
        return ChannelResult(
            timing_ms=timing,
            payload=np.array(payload)[order][:timing.size],
            receipts=np.stack([np.array(hops)[order][:timing.size],
                               np.array(gaps)[order][:timing.size],
                               np.array(accums)[order][:timing.size]],
                              axis=1))
