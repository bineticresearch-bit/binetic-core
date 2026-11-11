/* netcomputer_mix.p4
 *
 * TWO-REGISTER MIXING: the switch combines two independent input streams
 * carried in DIFFERENT packet fields into a single state. This is the
 * minimal construction that can produce a genuine nonlinear mixing of
 * two inputs, which a single-register recurrence cannot (each packet
 * updates only one accumulator from one value).
 *
 * Input channels:
 *   IP ID low byte   -> u[t]   (stream A, this packet)
 *   IP TTL override  -> v[t]   (stream B, this packet)
 *
 * Registers:
 *   reg_u:  running value of stream A (delayed by one packet)
 *   reg_v:  running value of stream B (delayed by one packet)
 *   mix:    the PRODUCT reg_u * reg_v -> the mixing term
 *   mix_sum: accumulating sum of mixings
 *
 * The receipt reports u_prev, v_prev, and the running mixing term, so a
 * readout sees nonlinear combinations of consecutive inputs.
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
    bit<32> v_prev;
    bit<32> mix;
}
struct headers_t { ethernet_t eth; ipv4_t ip; receipt_t receipt; }
struct metadata_t { bit<48> ingress_ts; }

parser my_parser(packet_in pkt, out headers_t hdr,
                 inout metadata_t md, inout standard_metadata_t sm) {
    state start { pkt.extract(hdr.eth);
        transition select(hdr.eth.ethertype) {
            16w0x0800: parse_ipv4; default: accept; } }
    state parse_ipv4 { pkt.extract(hdr.ip);
        transition select(hdr.ip.protocol) {
            253: parse_receipt; default: accept; } }
    state parse_receipt { pkt.extract(hdr.receipt); transition accept; }
}

control my_verify_checksum(inout headers_t hdr, inout metadata_t md) { apply { } }

control my_ingress(inout headers_t hdr, inout metadata_t md,
                   inout standard_metadata_t sm) {
    register<bit<32>>(64) reg_u;      /* stream A, delayed */
    register<bit<32>>(64) reg_v;      /* stream B, delayed */
    register<bit<32>>(64) mix_sum;    /* accumulated mixings */
    register<bit<32>>(64) count_reg;

    apply {
        bit<32> pidx = (bit<32>)sm.ingress_port;

        bit<32> u_prev;
        reg_u.read(u_prev, pidx);
        bit<32> v_prev;
        reg_v.read(v_prev, pidx);
        bit<32> msum;
        mix_sum.read(msum, pidx);
        bit<32> cnt;
        count_reg.read(cnt, pidx);

        /* two independent streams carried in different fields */
        bit<32> u = (bit<32>)hdr.ip.id[7:0];       /* stream A */
        bit<32> v = (bit<32>)hdr.ip.ttl;          /* stream B */

        /* the MIXING: product of the two delayed streams */
        bit<64> prod = ((bit<64>)u_prev * (bit<64>)v_prev) >> 8;
        bit<64> cap = (bit<64>)32w0xFFFFFFFE;
        bit<32> mixv = (prod > cap) ? 32w0xFFFFFFFE : (bit<32>)prod;
        mix_sum.write(pidx, msum + mixv);

        /* advance the delays */
        reg_u.write(pidx, u);
        reg_v.write(pidx, v);
        count_reg.write(pidx, cnt + 1);

        hdr.receipt.count  = cnt + 1;
        hdr.receipt.u_prev = u_prev;
        hdr.receipt.v_prev = v_prev;
        hdr.receipt.mix    = mixv;

        /* keep TTL sane for forwarding; the stream-B input is in the
           orig TTL field before we overwrite it */
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
    apply { pkt.emit(hdr.eth); pkt.emit(hdr.ip); pkt.emit(hdr.receipt); }
}

V1Switch(my_parser(), my_verify_checksum(), my_ingress(),
         my_egress(), my_compute_checksum(), my_deparser()) main;
