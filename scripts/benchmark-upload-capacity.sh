#!/usr/bin/env bash
# ============================================================
# RNAseek — Production Upload Capacity Benchmark
# ============================================================
# Safe, non-disruptive commands to measure:
#   1. Inbound network bandwidth (iperf3 to public server)
#   2. Disk I/O write speed on the NFS/media mount
#
# Run as: bash scripts/benchmark-upload-capacity.sh
# Prereqs: iperf3, dd, fio (optional)
# ============================================================

set -euo pipefail

MEDIA_MOUNT="${MEDIA_ROOT:-/app/media}"
BENCH_DIR="$MEDIA_MOUNT/.benchmark"
RESULTS_FILE="$BENCH_DIR/results-$(date +%Y%m%d-%H%M%S).txt"

echo "============================================================"
echo " RNAseek Upload Capacity Benchmark"
echo " Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo " Media Mount: $MEDIA_MOUNT"
echo "============================================================"
echo ""

mkdir -p "$BENCH_DIR"

# ── 1. Network Bandwidth ──────────────────────────────────────
echo "=== 1. NETWORK BANDWIDTH ==="
echo ""
echo "Option A: iperf3 (most accurate)"
echo "  You need a second machine or public iperf3 server."
echo "  From a remote machine with good bandwidth, run:"
echo "    iperf3 -s                       # start server"
echo "  Then on THIS server, run:"
echo "    iperf3 -c <REMOTE_IP> -R -t 10  # -R = reverse (measures inbound)"
echo ""
echo "Option B: Public speed test (quick estimate)"

if command -v iperf3 &>/dev/null; then
    echo "  iperf3 is installed. Testing against public server..."
    echo "  (Using bouygues.iperf.fr — a well-known public iperf3 server)"
    echo ""
    # -R = reverse mode (server sends TO us = measures our download/inbound)
    # -t 10 = 10 second test
    # -P 4 = 4 parallel streams (saturate the link)
    iperf3 -c bouygues.iperf.fr -R -t 10 -P 4 2>&1 || echo "  Public iperf3 server unavailable. Try: iperf3 -c <YOUR_TEST_SERVER> -R -t 10"
    echo ""
else
    echo "  iperf3 not installed. Install with: sudo apt-get install -y iperf3"
    echo ""
fi

echo "Option C: curl-based download test (rough estimate)"
echo "  Downloading 100MB test file to measure raw bandwidth..."
CURL_START=$(date +%s%N)
curl -so /dev/null --max-time 30 https://speed.hetzner.de/100MB.bin 2>&1 || true
CURL_END=$(date +%s%N)
CURL_MS=$(( (CURL_END - CURL_START) / 1000000 ))
if [ "$CURL_MS" -gt 0 ]; then
    # 100 MB = 800 Mbit
    CURL_MBPS=$(echo "scale=1; 800000 / $CURL_MS" | bc 2>/dev/null || echo "N/A")
    echo "  100 MB downloaded in ${CURL_MS} ms ≈ ${CURL_MBPS} Mbps"
fi
echo ""

# ── 2. Disk I/O Write Speed (dd) ──────────────────────────────
echo "=== 2. DISK I/O WRITE SPEED (dd) ==="
echo "  Testing sequential write to: $MEDIA_MOUNT"
echo ""

TESTFILE="$BENCH_DIR/dd_testfile"

echo "  Test A: 1 GB sequential write (throughput, bypasses page cache)"
dd if=/dev/zero of="$TESTFILE" bs=1M count=1024 conv=fdatasync oflag=direct 2>&1 | tail -1
rm -f "$TESTFILE"
echo ""

echo "  Test B: 256 MB write with sync (measures commit latency)"
dd if=/dev/zero of="$TESTFILE" bs=1M count=256 conv=fdatasync 2>&1 | tail -1
rm -f "$TESTFILE"
echo ""

# ── 3. Disk I/O Write Speed (fio — if available) ─────────────
echo "=== 3. DISK I/O WRITE SPEED (fio — detailed) ==="

if command -v fio &>/dev/null; then
    echo "  Running fio sequential write benchmark (simulates upload writes)..."
    echo ""

    # Sequential write, 1MB blocks, 1GB total — mimics large file upload chunks
    fio --name=seq_write \
        --directory="$BENCH_DIR" \
        --rw=write \
        --bs=1m \
        --size=1g \
        --numjobs=1 \
        --runtime=30 \
        --time_based \
        --direct=1 \
        --ioengine=libaio \
        --group_reporting \
        --output-format=normal \
        2>&1 | grep -E "(WRITE:|write:|bw=|iops=)"

    echo ""
    echo "  Running fio parallel write benchmark (simulates concurrent uploads)..."
    echo ""

    # 4 parallel writers — simulates multiple simultaneous uploads
    fio --name=parallel_write \
        --directory="$BENCH_DIR" \
        --rw=write \
        --bs=1m \
        --size=512m \
        --numjobs=4 \
        --runtime=30 \
        --time_based \
        --direct=1 \
        --ioengine=libaio \
        --group_reporting \
        --output-format=normal \
        2>&1 | grep -E "(WRITE:|write:|bw=|iops=)"

    # Clean up fio test files
    rm -f "$BENCH_DIR"/seq_write* "$BENCH_DIR"/parallel_write*
    echo ""
else
    echo "  fio not installed. Install with: sudo apt-get install -y fio"
    echo "  (dd results above are sufficient for a baseline)"
    echo ""
fi

# ── 4. NFS Mount Info ─────────────────────────────────────────
echo "=== 4. MOUNT INFO ==="
echo "  Mount point details for $MEDIA_MOUNT:"
mount | grep -E "$(df "$MEDIA_MOUNT" | tail -1 | awk '{print $1}')" 2>/dev/null || df -hT "$MEDIA_MOUNT"
echo ""
echo "  Filesystem stats:"
df -hT "$MEDIA_MOUNT"
echo ""

# ── Cleanup ───────────────────────────────────────────────────
rmdir "$BENCH_DIR" 2>/dev/null || true

echo "============================================================"
echo " HOW TO INTERPRET THESE RESULTS"
echo "============================================================"
echo ""
echo " Your maximum upload speed is limited by the SLOWEST of:"
echo ""
echo "   1. Network bandwidth (iperf3 / curl result)"
echo "      e.g., 1 Gbps link → ~120 MB/s theoretical max"
echo ""
echo "   2. Disk write speed (dd / fio result)"
echo "      e.g., NFS at 200 MB/s → not the bottleneck"
echo "      e.g., NFS at 50 MB/s  → THIS is the bottleneck"
echo ""
echo "   3. Software overhead (Nginx buffering, tusd processing)"
echo "      Typically 5-15% overhead on top of raw I/O"
echo ""
echo " EXAMPLE:"
echo "   Network: 1 Gbps   = ~120 MB/s"
echo "   Disk:    NFS       = ~80 MB/s"
echo "   → Max upload speed ≈ 80 MB/s (disk-bound)"
echo "   → With software overhead ≈ 68-76 MB/s realistic"
echo ""
echo " COMMON BOTTLENECKS:"
echo "   - Nginx proxy_request_buffering ON → double-writes to disk"
echo "   - NFS mounted with sync (not async) → every write waits for ACK"
echo "   - Small chunk sizes → excessive HTTP overhead"
echo "   - TCP buffer sizes too small for high-latency links"
echo "============================================================"
