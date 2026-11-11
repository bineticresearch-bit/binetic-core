/* netcomputer_clf.p4 — closed-loop feedback, 16-bit channel.
 *
 * Frame layout (CRITICAL — the deparser emits in this order):
 *   eth(14) | ip(20) | receipt(20) | payload(fb_wide 2B, u_byte 1B, d_byte 1B)
 *
 * The receipt is written by the switch and occupies the FIRST 20 payload
 * bytes; input data must follow it. The parser reads the payload header
 * AFTER the receipt, so its fields land at payload offsets 20..23.
 */

#include <core.p4>
#include <v1model.p4>

const bit<9> FORWARD_PORT = 1;

header ethernet_t {
    bit<48> dst_addr; bit<48> src_addr; bit<16> ethertype;
}
header ipv4_t {
    bit<4> version; bit<4> ihl; bit<8> diffserv; bit<16> total_len;
    bit<16> id; bit<3> flags; bit<13> frag_off; bit<8> ttl;
    bit<8> protocol; bit<16> checksum; bit<32> src; bit<32> dst;
}
header receipt_t {
    bit<32> count;
    bit<32> u_prev;
    bit<32> f_prev;
    bit<32> mix_uf;
    bit<32> mix_ud;
}
header payload_t {
    bit<16> fb_wide;      /* 16-bit feedback */
    bit<8>  u_byte;       /* exogenous input */
    bit<8>  d_byte;       /* second delayed stream */
}

struct headers_t { ethernet_t eth; ipv4_t ip; receipt_t receipt; payload_t pl; }
struct metadata_t { bit<48> ingress_ts; }

parser my_parser(packet_in pkt, out headers_t hdr,
                 inout metadata_t md, inout standard_metadata_t sm) {
    state start { pkt.extract(hdr.eth);
        transition select(hdr.eth.ethertype) {
            16w0x0800: parse_ipv4; default: accept; } }
    state parse_ipv4 { pkt.extract(hdr.ip);
        transition select(hdr.ip.protocol) {
            253: parse_receipt; default: accept; } }
    state parse_receipt { pkt.extract(hdr.receipt);
        transition parse_payload; }
    state parse_payload { pkt.extract(hdr.pl); transition accept; }
}

control my_verify_checksum(inout headers_t hdr, inout metadata_t md) { apply { } }

control my_ingress(inout headers_t hdr, inout metadata_t md,
                   inout standard_metadata_t sm) {
    register<bit<32>>(64) reg_u;
    register<bit<32>>(64) reg_f;
    register<bit<32>>(64) reg_d;
    register<bit<32>>(64) count_reg;

    apply {
        bit<32> pidx = (bit<32>)sm.ingress_port;

        bit<32> u_p; reg_u.read(u_p, pidx);
        bit<32> f_p; reg_f.read(f_p, pidx);
        bit<32> d_p; reg_d.read(d_p, pidx);
        bit<32> cnt; count_reg.read(cnt, pidx);

        bit<32> u = (bit<32>)hdr.pl.u_byte;
        bit<32> f = (bit<32>)hdr.pl.fb_wide;
        bit<32> d = (bit<32>)hdr.pl.d_byte;

        bit<64> p_uf = ((bit<64>)u_p * (bit<64>)f_p) >> 16;
        bit<64> p_ud = ((bit<64>)u_p * (bit<64>)d_p) >> 8;
        bit<64> cap = (bit<64>)32w0xFFFFFFFE;
        bit<32> m_uf = (p_uf > cap) ? 32w0xFFFFFFFE : (bit<32>)p_uf;
        bit<32> m_ud = (p_ud > cap) ? 32w0xFFFFFFFE : (bit<32>)p_ud;

        reg_u.write(pidx, u);
        reg_f.write(pidx, f);
        reg_d.write(pidx, d);
        count_reg.write(pidx, cnt + 1);

        hdr.receipt.count  = cnt + 1;
        hdr.receipt.u_prev = u_p;
        hdr.receipt.f_prev = f_p;
        hdr.receipt.mix_uf = m_uf;
        hdr.receipt.mix_ud = m_ud;

        hdr.ip.ttl = (bit<8>)(cnt + 1);
        sm.egress_spec = FORWARD_PORT;
    }
}

control my_egress(inout headers_t hdr, inout metadata_t md,
                  inout standard_metadata_t sm) { apply { } }

control my_compute_checksum(inout headers_t hdr, inout metadata_t md) {
    apply {
        update_checksum(hdr.ip.isValid(),
            { hdr.ip.version, hdr.ip.ihl, hdr.ip.diffserv, hdr.ip.total_len,
              hdr.ip.id, hdr.ip.flags, hdr.ip.frag_off, hdr.ip.ttl,
              hdr.ip.protocol, hdr.ip.src, hdr.ip.dst },
            hdr.ip.checksum, HashAlgorithm.csum16);
    }
}

control my_deparser(packet_out pkt, in headers_t hdr) {
    apply {
        pkt.emit(hdr.eth);
        pkt.emit(hdr.ip);
        pkt.emit(hdr.receipt);
        pkt.emit(hdr.pl);
    }
}

V1Switch(my_parser(), my_verify_checksum(), my_ingress(),
         my_egress(), my_compute_checksum(), my_deparser()) main;
