"""
Extract TLS ClientHello fingerprints (JA3 + raw components) from a pcap.

Passive by design. Nothing here sits in the request path: tcpdump writes
pcaps, this reads them afterwards. That matters for this project because the
dependent variable is timing -- an inline TLS-inspecting proxy would perturb
the very latencies Phase III measures, and a capture process cannot.

What it produces, one row per observed ClientHello:

    timestamp, src_ip, src_port, dst_ip, dst_port,
    tls_version, sni, alpn, ja3, ja3_hash,
    ciphers, extensions, curves, ec_point_formats, sig_algs,
    n_ciphers, n_extensions, has_grease, key_share_groups

`src_ip` + `src_port` is the join key back to a Charweb session: nginx passes
the client port through as a header and `/api/track` records it on
UserSession, so a TLS connection maps to a session exactly rather than by
fuzzy IP+time matching. See join_tls_sessions.py.

On JA3 vs JA4
-------------
JA3 is implemented here exactly (MD5 over
version,ciphers,extensions,curves,point_formats with GREASE stripped), so
hashes are comparable with any other JA3 tool.

JA4 is deliberately NOT computed. Its digest layout is fiddly enough that a
near-miss implementation produces hashes that silently fail to match every
other tool -- worse than absent. The raw components JA4 is built from
(ciphers, extensions, sig_algs, ALPN, SNI presence) are all emitted, so JA4
can be added later against FoxIO's reference implementation, and the raw
fields are directly usable as features regardless.

Usage
-----
    python extract_ja3.py capture.pcap -o tls_fingerprints.csv
    python extract_ja3.py 'captures/*.pcap' -o tls_fingerprints.csv

Requirements: pip install dpkt
"""
import argparse
import csv
import glob
import hashlib
import struct
import sys
from collections import defaultdict

import dpkt

# GREASE values (RFC 8701): 0x0a0a, 0x1a1a, ... 0xfafa. Clients inject these
# at random into cipher/extension/group lists specifically so that servers
# don't ossify on a fixed set, which means they must be stripped or the
# fingerprint changes on every connection from the same client.
GREASE = {0x0a0a, 0x1a1a, 0x2a2a, 0x3a3a, 0x4a4a, 0x5a5a, 0x6a6a, 0x7a7a,
          0x8a8a, 0x9a9a, 0xaaaa, 0xbaba, 0xcaca, 0xdada, 0xeaea, 0xfafa}

TLS_HANDSHAKE = 0x16
CLIENT_HELLO = 0x01

EXT_SNI = 0x0000
EXT_EC_GROUPS = 0x000a
EXT_EC_POINT_FMT = 0x000b
EXT_SIG_ALGS = 0x000d
EXT_ALPN = 0x0010
EXT_KEY_SHARE = 0x0033

# A ClientHello carrying post-quantum key shares (X25519MLKEM768 and friends)
# runs well past a single MTU, so hellos routinely span two or three TCP
# segments now. Parsing only the first packet would silently miss exactly the
# modern clients this study cares about -- hence the reassembly buffer below.
MAX_BUFFER = 65536


def _u16(b, i):
    return struct.unpack('!H', b[i:i + 2])[0]


def _u16_list(b):
    """Parse a byte string as a sequence of uint16."""
    return [struct.unpack('!H', b[i:i + 2])[0] for i in range(0, len(b) - 1, 2)]


def _strip_grease(values):
    return [v for v in values if v not in GREASE]


def parse_client_hello(data):
    """Parse a TLS record containing a ClientHello. Returns a dict, or None if
    `data` is not a complete ClientHello (caller should buffer more)."""
    if len(data) < 5 or data[0] != TLS_HANDSHAKE:
        return None

    rec_len = _u16(data, 3)
    if len(data) < 5 + rec_len:
        return None                              # incomplete -- need more segments
    body = data[5:5 + rec_len]

    if len(body) < 4 or body[0] != CLIENT_HELLO:
        return None
    hs_len = int.from_bytes(body[1:4], 'big')
    hello = body[4:4 + hs_len]
    if len(hello) < 34:
        return None

    i = 0
    legacy_version = _u16(hello, i); i += 2
    i += 32                                       # random

    sid_len = hello[i]; i += 1 + sid_len          # legacy_session_id

    if i + 2 > len(hello):
        return None
    cs_len = _u16(hello, i); i += 2
    ciphers = _strip_grease(_u16_list(hello[i:i + cs_len])); i += cs_len

    if i >= len(hello):
        return None
    comp_len = hello[i]; i += 1 + comp_len        # compression methods

    out = {
        'tls_version': legacy_version,
        'ciphers': ciphers,
        'extensions': [], 'curves': [], 'ec_point_formats': [],
        'sig_algs': [], 'key_share_groups': [],
        'sni': '', 'alpn': '', 'has_grease': False,
    }

    if i + 2 > len(hello):                        # no extensions block (rare)
        return out
    ext_total = _u16(hello, i); i += 2
    end = min(i + ext_total, len(hello))

    ext_types = []
    while i + 4 <= end:
        etype = _u16(hello, i); elen = _u16(hello, i + 2); i += 4
        edata = hello[i:i + elen]; i += elen
        ext_types.append(etype)
        if etype in GREASE:
            out['has_grease'] = True
            continue

        if etype == EXT_EC_GROUPS and len(edata) >= 2:
            out['curves'] = _strip_grease(_u16_list(edata[2:2 + _u16(edata, 0)]))
        elif etype == EXT_EC_POINT_FMT and len(edata) >= 1:
            out['ec_point_formats'] = list(edata[1:1 + edata[0]])
        elif etype == EXT_SIG_ALGS and len(edata) >= 2:
            out['sig_algs'] = _strip_grease(_u16_list(edata[2:2 + _u16(edata, 0)]))
        elif etype == EXT_SNI and len(edata) >= 5:
            # server_name_list -> entry(type=0, len, host)
            nlen = _u16(edata, 3)
            out['sni'] = edata[5:5 + nlen].decode('ascii', 'replace')
        elif etype == EXT_ALPN and len(edata) >= 3:
            protos, j = [], 2
            while j < len(edata):
                plen = edata[j]; j += 1
                protos.append(edata[j:j + plen].decode('ascii', 'replace')); j += plen
            out['alpn'] = ','.join(protos)
        elif etype == EXT_KEY_SHARE and len(edata) >= 2:
            groups, j = [], 2
            while j + 4 <= len(edata):
                g = _u16(edata, j); klen = _u16(edata, j + 2); j += 4 + klen
                if g not in GREASE:
                    groups.append(g)
            out['key_share_groups'] = groups

    out['has_grease'] = out['has_grease'] or any(e in GREASE for e in ext_types)
    out['extensions'] = _strip_grease(ext_types)
    return out


def ja3_string(h):
    """JA3: version,ciphers,extensions,curves,point_formats -- dash-separated
    within each field, comma-separated between."""
    return ','.join([
        str(h['tls_version']),
        '-'.join(str(c) for c in h['ciphers']),
        '-'.join(str(e) for e in h['extensions']),
        '-'.join(str(c) for c in h['curves']),
        '-'.join(str(p) for p in h['ec_point_formats']),
    ])


def ja3_hash(s):
    return hashlib.md5(s.encode()).hexdigest()


def _iter_tcp(pcap_path):
    """Yield (ts, src_ip, sport, dst_ip, dport, payload) for TCP packets."""
    import socket as _s
    with open(pcap_path, 'rb') as f:
        try:
            reader = dpkt.pcap.Reader(f)
        except ValueError:
            f.seek(0)
            reader = dpkt.pcapng.Reader(f)
        for ts, buf in reader:
            try:
                eth = dpkt.ethernet.Ethernet(buf)
                ip = eth.data
                if isinstance(ip, bytes):        # e.g. Linux cooked / raw IP
                    continue
                if not isinstance(ip, (dpkt.ip.IP, dpkt.ip6.IP6)):
                    continue
                tcp = ip.data
                if not isinstance(tcp, dpkt.tcp.TCP) or not tcp.data:
                    continue
                fam = _s.AF_INET if isinstance(ip, dpkt.ip.IP) else _s.AF_INET6
                yield (ts, _s.inet_ntop(fam, ip.src), tcp.sport,
                       _s.inet_ntop(fam, ip.dst), tcp.dport, bytes(tcp.data))
            except Exception:
                continue


def extract(pcap_path):
    """Yield one fingerprint dict per ClientHello seen in the pcap."""
    buffers = defaultdict(bytes)
    first_ts = {}
    done = set()

    for ts, sip, sport, dip, dport, payload in _iter_tcp(pcap_path):
        flow = (sip, sport, dip, dport)
        if flow in done:
            continue
        if not buffers[flow] and payload[0] != TLS_HANDSHAKE:
            continue                              # not the start of a handshake
        if flow not in first_ts:
            first_ts[flow] = ts

        buffers[flow] += payload
        if len(buffers[flow]) > MAX_BUFFER:
            done.add(flow); buffers.pop(flow, None); continue

        h = parse_client_hello(buffers[flow])
        if h is None:
            continue                              # incomplete, keep buffering

        s = ja3_string(h)
        yield {
            'timestamp': f'{first_ts[flow]:.6f}',
            'src_ip': sip, 'src_port': sport, 'dst_ip': dip, 'dst_port': dport,
            'tls_version': h['tls_version'],
            'sni': h['sni'], 'alpn': h['alpn'],
            'ja3': s, 'ja3_hash': ja3_hash(s),
            'ciphers': '-'.join(map(str, h['ciphers'])),
            'extensions': '-'.join(map(str, h['extensions'])),
            'curves': '-'.join(map(str, h['curves'])),
            'ec_point_formats': '-'.join(map(str, h['ec_point_formats'])),
            'sig_algs': '-'.join(map(str, h['sig_algs'])),
            'key_share_groups': '-'.join(map(str, h['key_share_groups'])),
            'n_ciphers': len(h['ciphers']),
            'n_extensions': len(h['extensions']),
            'has_grease': int(h['has_grease']),
        }
        done.add(flow); buffers.pop(flow, None)


FIELDS = ['timestamp', 'src_ip', 'src_port', 'dst_ip', 'dst_port',
          'tls_version', 'sni', 'alpn', 'ja3', 'ja3_hash',
          'ciphers', 'extensions', 'curves', 'ec_point_formats', 'sig_algs',
          'key_share_groups', 'n_ciphers', 'n_extensions', 'has_grease']


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pcap', nargs='+', help='pcap file(s); globs are expanded')
    ap.add_argument('-o', '--out', default='tls_fingerprints.csv')
    args = ap.parse_args()

    paths = []
    for p in args.pcap:
        paths.extend(sorted(glob.glob(p)) or [p])

    rows = []
    for p in paths:
        try:
            n = 0
            for row in extract(p):
                rows.append(row); n += 1
            print(f'{p}: {n} ClientHello(s)')
        except FileNotFoundError:
            print(f'{p}: not found', file=sys.stderr)

    if not rows:
        print('No ClientHellos found. Check the capture filter -- it must keep '
              'the FULL handshake packets (modern hellos span multiple '
              'segments), not just the first.', file=sys.stderr)
        return 1

    with open(args.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)

    uniq = len({r['ja3_hash'] for r in rows})
    print(f'\n{len(rows)} handshakes, {uniq} distinct JA3 fingerprints')
    print(f'Wrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
