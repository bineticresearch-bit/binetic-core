/* netcomputer_cascade.p4
 *
 * CASCADE protocol version: one packet's register state writes into the
 * NEXT packet. This is the only version with genuine cross-packet
 * recurrence - a true reservoir rather than a per-packet channel.
 *
 * The register holds a lattice-like state: each packet's phase value is
 * multiplied into an accumulator, and the accumulator's value is written
 * into the outgoing packet's receipt. So packet t+1 sees the state that
 * packet t left behind, and the returned receipt is a nonlinear
 * (saturating) function of the entire input history.
 *
 * Registers:
 *   phase_acc  - running phase: multiplied by each packet's input value
 *   count      - packet counter (exact, verified pattern)
 *   last_id    - previous packet's input byte
 *   history[N] - ring buffer of the last N input bytes
 */

#include <core.p4>
#include <v1model.p4>

const bit<9> FORWARD_PORT = 1;
const bit<8> HISTORY_LEN = 8;

header ethernet_t {
    bit<48> dst_addr;
    bit<48> src_addr;
    bit<16> ethertype;
}

header ipv4_t {
    bit<4>  version;
    bit<4>  ihl;
    bit<8>  diffserv;
    bit<16> total_len;
    bit<16> id;
    bit<3>  flags;
    bit<13> frag_off;
    bit<8>  ttl;
    bit<8>  protocol;
    bit<16> checksum;
    bit<32> src;
    bit<32> dst;
}

/* Receipt written INTO the payload (first 16 bytes, switch-overwritten). */
header receipt_t {
    bit<32> count;        /* packet counter */
    bit<32> phase;        /* the accumulated phase: the reservoir state */
    bit<32> delay_ns;     /* switch-measured inter-arrival time */
    bit<32> history_xor;  /* XOR of recent input bytes: parity memory */
}

struct headers_t {
    ethernet_t eth;
    ipv4_t     ip;
    receipt_t  receipt;
}

struct metadata_t {
    bit<48> ingress_ts;
}

parser my_parser(packet_in pkt, out headers_t hdr,
                 inout metadata_t md, inout standard_metadata_t sm) {
    state start {
        pkt.extract(hdr.eth);
        transition select(hdr.eth.ethertype) {
            16w0x0800: parse_ipv4;
            default: accept;
        }
    }
    state parse_ipv4 {
        pkt.extract(hdr.ip);
        transition select(hdr.ip.protocol) {
            253: parse_receipt;
            default: accept;
        }
    }
    state parse_receipt {
        pkt.extract(hdr.receipt);
        transition accept;
    }
}

control my_verify_checksum(inout headers_t hdr, inout metadata_t md) {
    apply { }
}

control my_ingress(inout headers_t hdr, inout metadata_t md,
                   inout standard_metadata_t sm) {
    /* Cross-packet state: these persist between packets. */
    register<bit<32>>(64) phase_acc;     /* running product accumulator */
    register<bit<32>>(64) count_reg;     /* packet counter */
    register<bit<32>>(64) last_arrival;  /* previous arrival timestamp */
    register<bit<32>>(8)  history;       /* ring of last 8 input bytes */

    apply {
        bit<32> pidx = (bit<32>)sm.ingress_port;
        bit<48> ts = sm.ingress_global_timestamp;

        /* --- read the persistent state this packet was born into --- */
        bit<32> acc;
        phase_acc.read(acc, pidx);

        bit<32> cnt;
        count_reg.read(cnt, pidx);

        bit<32> prev_ts;
        last_arrival.read(prev_ts, pidx);

        /* --- the input: first payload byte AFTER the 16-byte receipt --- */
        /* The receipt occupies payload[0..15]; input at payload[16]. We
           cannot index into the payload from here directly, so we use the
           IPv4 ID field as the input carrier (the sender sets it). */
        bit<8> input_val = (bit<8>)hdr.ip.id[7:0];

        /* --- the recurrence: multiply the input into the accumulator.
              This is a cross-packet nonlinear map. Saturating arithmetic
              emulates the clip of a physical register. --- */
        /* map [0,255] -> [0,65535] by shifts: val * 257 ~= val * 65535/255 */
        bit<32> scaled = ((bit<32>)input_val << 8) + (bit<32>)input_val;
        bit<32> new_acc;
        if (acc == 0) {
            new_acc = scaled + 1;
        } else {
            /* fixed-point multiply, then shift right to bound growth */
            bit<64> prod = ((bit<64>)acc * (bit<64>)scaled) >> 16;
            bit<64> cap = (bit<64>)32w0xFFFFFFFE;
            if (prod > cap) {
                new_acc = 32w0xFFFFFFFE;
            } else {
                new_acc = (bit<32>)prod;
            }
        }
        phase_acc.write(pidx, new_acc);

        /* --- parity memory: XOR the input into the history ring --- */
        bit<32> idx = cnt % 8;
        bit<32> old;
        history.read(old, idx);
        history.write(idx, (bit<32>)input_val);
        bit<32> hxor = ((bit<32>)input_val) ^ old;

        /* --- inter-arrival delay (the analog dynamics) --- */
        bit<32> delay = (bit<32>)(ts - (bit<48>)prev_ts);
        last_arrival.write(pidx, (bit<32>)ts);

        count_reg.write(pidx, cnt + 1);

        /* --- write the RECEIPT into the payload --- */
        hdr.receipt.count       = cnt + 1;
        hdr.receipt.phase       = new_acc;
        hdr.receipt.delay_ns    = delay;
        hdr.receipt.history_xor = hxor;

        hdr.ip.ttl = (bit<8>)(cnt + 1);

        sm.egress_spec = FORWARD_PORT;
    }
}

control my_egress(inout headers_t hdr, inout metadata_t md,
                  inout standard_metadata_t sm) {
    apply { }
}

control my_compute_checksum(inout headers_t hdr, inout metadata_t md) {
    apply {
        update_checksum(hdr.ip.isValid(),
                        { hdr.ip.version, hdr.ip.ihl, hdr.ip.diffserv,
                          hdr.ip.total_len, hdr.ip.id, hdr.ip.flags,
                          hdr.ip.frag_off, hdr.ip.ttl, hdr.ip.protocol,
                          hdr.ip.src, hdr.ip.dst },
                        hdr.ip.checksum, HashAlgorithm.csum16);
    }
}

control my_deparser(packet_out pkt, in headers_t hdr) {
    apply {
        pkt.emit(hdr.eth);
        pkt.emit(hdr.ip);
        pkt.emit(hdr.receipt);
    }
}

V1Switch(my_parser(), my_verify_checksum(), my_ingress(),
         my_egress(), my_compute_checksum(), my_deparser()) main;
