#!/usr/bin/env bash
# Passive TLS handshake capture for Charweb data-collection windows.
#
# Records only enough of each connection to recover the ClientHello, then
# research/extract_ja3.py turns the pcaps into fingerprints offline. Nothing
# here touches the request path: if this process dies, the site is unaffected,
# and it cannot add latency to the timings Phase III measures.
#
# Usage:
#   sudo ./capture_tls.sh start            # begin capturing
#   sudo ./capture_tls.sh stop             # end capture
#   sudo ./capture_tls.sh status
#
# Environment:
#   IFACE     interface to capture on   (default: auto-detected default route)
#   OUTDIR    where pcaps are written   (default: /var/lib/charweb/captures)
#   ROTATE    seconds per file          (default: 3600)
#   KEEP      number of files to retain (default: 168 == 7 days at 1h)
set -euo pipefail

IFACE="${IFACE:-$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}')}"
OUTDIR="${OUTDIR:-/var/lib/charweb/captures}"
ROTATE="${ROTATE:-3600}"
KEEP="${KEEP:-168}"
PIDFILE=/run/charweb-tlscap.pid

# Keep only client->server traffic on 443 whose first payload byte is 0x16
# (TLS handshake). That drops all application data, so the capture contains
# handshakes and nothing else -- far smaller, and no request or response
# bodies are ever written to disk, which matters for human-subject data.
#
#   tcp[((tcp[12:1] & 0xf0) >> 2)] = 0x16   -> first payload byte is a
#                                              handshake record
FILTER='tcp dst port 443 and (tcp[((tcp[12:1] & 0xf0) >> 2)] = 0x16)'

case "${1:-}" in
  start)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "already running (pid $(cat "$PIDFILE"))"; exit 0
    fi
    [ -n "$IFACE" ] || { echo "could not detect interface; set IFACE=" >&2; exit 1; }
    mkdir -p "$OUTDIR"

    # -s 0     full packets: modern hellos with post-quantum key shares span
    #          several segments, and truncating them loses the fingerprint
    # -W/-G    rotate hourly, keep KEEP files, so disk use is bounded
    # -Z       drop root once the socket is open
    nohup tcpdump -i "$IFACE" -s 0 -n \
          -G "$ROTATE" -W "$KEEP" \
          -w "$OUTDIR/tls-%Y%m%d-%H%M%S.pcap" \
          -Z root "$FILTER" >"$OUTDIR/tcpdump.log" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "capturing on $IFACE -> $OUTDIR (rotate ${ROTATE}s, keep $KEEP)"
    else
      echo "failed to start; see $OUTDIR/tcpdump.log" >&2
      tail -5 "$OUTDIR/tcpdump.log" >&2; rm -f "$PIDFILE"; exit 1
    fi
    ;;

  stop)
    if [ -f "$PIDFILE" ]; then
      kill "$(cat "$PIDFILE")" 2>/dev/null || true
      rm -f "$PIDFILE"; echo "stopped"
    else
      echo "not running"
    fi
    ;;

  status)
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "running (pid $(cat "$PIDFILE")) on $IFACE"
      ls -lh "$OUTDIR"/tls-*.pcap 2>/dev/null | tail -5
      du -sh "$OUTDIR" 2>/dev/null
    else
      echo "not running"
    fi
    ;;

  *)
    echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
