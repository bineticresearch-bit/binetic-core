"""Direct stream verification with visible errors."""
import socket
import struct
import subprocess
import time


def csum(d):
    if len(d) % 2:
        d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d) // 2), d))
    s = (s >> 16) + (s & 0xFFFF)
    s += (s >> 16)
    return (~s) & 0xFFFF


RECV = r'''
import socket, struct, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.setsockopt(socket.SOL_SOCKET, 35, 1)
s.settimeout(5.0)
s.bind(("veth_out", 0))
for _ in range(8):
    try:
        pkt, anc, _, _ = s.recvmsg(2048, 1024)
    except socket.timeout:
        break
    if len(pkt) < 58: continue
    count, u_p, f_p, m_uf, m_ud = struct.unpack("!IIIII", pkt[34:54])
    fb, ub, db = struct.unpack("!HBB", pkt[54:58])
    print(f"{count} u_prev={u_p} f_prev={f_p} mix_uf={m_uf} mix_ud={m_ud} pl_fb={fb} pl_u={ub} pl_d={db}", flush=True)
'''


def main():
    us = [101, 102, 103, 104, 105, 106]
    fbs = [10000, 20000, 30000, 40000, 50000, 60000]
    ds = [51, 52, 53, 54, 55, 56]

    p = subprocess.Popen(["ip", "netns", "exec", "ns_out", "python3", "-c", RECV],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.0)

    send_code = r'''
import socket, struct, time, sys
def csum(d):
    if len(d) % 2: d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d)//2), d)); s=(s>>16)+(s&0xFFFF); s+=(s>>16)
    return (~s)&0xFFFF
us = [101, 102, 103, 104, 105, 106]
fbs = [10000, 20000, 30000, 40000, 50000, 60000]
ds = [51, 52, 53, 54, 55, 56]
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); s.bind(("veth_in", 0))
for i in range(6):
    payload = bytes(20) + struct.pack("!HBB", fbs[i], us[i], ds[i])
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20+len(payload), 0, 0, 64, 253, 0,
                     socket.inet_aton("10.0.1.1"), socket.inet_aton("10.0.1.2"))
    ip = ip[:10]+struct.pack("!H", csum(ip))+ip[12:]
    s.send(bytes.fromhex("020000000002")+bytes.fromhex("020000000001")
           + struct.pack("!H",0x0800) + ip + payload)
    time.sleep(0.05)
print("sent")
'''
    r = subprocess.run(["ip", "netns", "exec", "ns_in", "python3", "-c", send_code],
                       capture_output=True, text=True, timeout=30)
    print("sender:", r.stdout.strip(), r.stderr.strip()[:200])
    out, _ = p.communicate(timeout=15)
    print("receiver:")
    print(out)


if __name__ == "__main__":
    main()
