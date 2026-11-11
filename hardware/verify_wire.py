"""Minimal verification: does ns_out still see packets from ns_in?"""
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


def main():
    rcv_code = r'''
import socket, sys
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0800))
s.setsockopt(socket.SOL_SOCKET, 35, 1)
s.settimeout(5.0)
s.bind(("veth_out", 0))
n = 0
while True:
    try:
        pkt, anc, _, _ = s.recvmsg(2048, 1024)
    except socket.timeout:
        break
    n += 1
print("got", n)
'''
    send_code = r'''
import socket, struct, time
def csum(d):
    if len(d) % 2: d += b"\x00"
    s = sum(struct.unpack("!%dH" % (len(d)//2), d))
    s=(s>>16)+(s&0xFFFF); s+=(s>>16)
    return (~s)&0xFFFF
s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW); s.bind(("veth_in", 0))
for i in range(3):
    p = bytes(16) + bytes([42])
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 10, 20+len(p), (i & 0xFF00)|42, 0,
                     64, 253, 0, socket.inet_aton("10.0.1.1"), socket.inet_aton("10.0.1.2"))
    ip = ip[:10] + struct.pack("!H", csum(ip)) + ip[12:]
    s.send(bytes.fromhex("020000000002") + bytes.fromhex("020000000001")
           + struct.pack("!H", 0x0800) + ip + p)
    time.sleep(0.02)
print("sent 3")
'''
    p = subprocess.Popen(["ip", "netns", "exec", "ns_out", "python3", "-c", rcv_code],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(1.0)
    r = subprocess.run(["ip", "netns", "exec", "ns_in", "python3", "-c", send_code],
                       capture_output=True, text=True, timeout=30)
    print(r.stdout.strip())
    out, _ = p.communicate(timeout=15)
    print("receiver:", out.strip())


if __name__ == "__main__":
    main()
