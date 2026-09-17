#!/usr/bin/env bash
# script_pyflate.sh — Section 10 deliverable for the pyflate benchmark.
#
# Runs the full pipeline documented in README.md (Sections 1, 4, 5, 7, 8)
# end to end: environment setup, baseline perf profiling + flame graph, the
# post-optimization (stdlib bz2 swap) run, and a before/after comparison.
#
# Usage (run from inside the VM; paths resolve relative to this script's
# own location, so it works from anywhere):
#   ./script_pyflate.sh
#
# Re-running this script regenerates perf.data / perf_report_*.txt /
# flamegraph_*.svg / perf_stat_*.txt in original/ and optimized/,
# overwriting whatever is currently there with a fresh run (numbers will
# vary slightly run to run — see COMPARISON.md for the committed reference
# numbers this repo's write-up is based on).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FLAMEGRAPH_DIR="$REPO_ROOT/tools/FlameGraph"

echo "############################################"
echo "# [1/4] Environment setup"
echo "############################################"

for tool in git perf python3 python3-dbg pip3; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "Missing required tool: $tool" >&2
    if [ "$tool" = "python3-dbg" ]; then
      echo "  Install with: apt-get update && apt-get install -y python3-dbg" >&2
    fi
    exit 1
  }
done

python3-dbg -c "import pyperf" >/dev/null 2>&1 || pip3 install pyperf pyperformance

if [ ! -d "$FLAMEGRAPH_DIR" ]; then
  echo "Cloning FlameGraph tools into $FLAMEGRAPH_DIR ..."
  git clone https://github.com/brendangregg/FlameGraph.git "$FLAMEGRAPH_DIR"
fi

# parse_seconds <run_output_file>: pulls pyperf's "Mean +- std dev: X unit"
# line out of a captured run and prints the mean converted to seconds.
parse_seconds() {
  python3 - "$1" <<'PYEOF'
import re, sys
text = open(sys.argv[1]).read()
m = re.search(r"Mean \+- std dev: ([0-9.]+) (\w+) \+- ([0-9.]+) (\w+)", text)
if not m:
    print("nan")
    sys.exit(0)
val, unit = float(m.group(1)), m.group(2)
scale = {"sec": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
print(val * scale.get(unit, 1.0))
PYEOF
}

# profile_variant <dir> <tag>: perf record (call-graph) + report + flame
# graph + perf stat against <dir>/run_benchmark.py, writing
# perf_report_<tag>.txt / flamegraph_<tag>.svg / perf_stat_<tag>.txt /
# run_output_<tag>.txt into <dir>.
profile_variant() {
  local dir="$1" tag="$2"
  echo
  echo "=== Profiling '$tag' (in $dir) ==="
  (
    cd "$dir"

    echo "--- perf record (call-graph, cpu-clock) ---"
    perf record -F 999 -g -e cpu-clock -- python3-dbg run_benchmark.py \
      2>&1 | tee "run_output_${tag}.txt"
    perf report --stdio >"perf_report_${tag}.txt"

    echo "--- flame graph ---"
    perf script -i perf.data >out.perf
    "$FLAMEGRAPH_DIR/stackcollapse-perf.pl" out.perf >out.folded
    "$FLAMEGRAPH_DIR/flamegraph.pl" out.folded >"flamegraph_${tag}.svg"
    rm -f out.perf out.folded

    echo "--- perf stat (hardware counters) ---"
    perf stat -e cycles,instructions,cache-references,cache-misses,branch-instructions,branch-misses,bus-cycles,ref-cycles \
      -- python3-dbg run_benchmark.py >/dev/null 2>"perf_stat_${tag}.txt"
  )
}

echo
echo "############################################"
echo "# [2/4] Baseline run: original/"
echo "############################################"
profile_variant "$SCRIPT_DIR/original" "original"

echo
echo "--- software-event sampling (baseline only, per README Section 4) ---"
(
  cd "$SCRIPT_DIR/original"
  perf record -c 1 -g -e page-faults,minor-faults,context-switches,cpu-migrations \
    -o perf_swevents.data -- python3-dbg run_benchmark.py
  perf report --stdio -i perf_swevents.data >perf_report_original_swevents.txt
)

echo
echo "############################################"
echo "# [3/4] Post-optimization run: optimized/ (stdlib bz2 swap)"
echo "############################################"
profile_variant "$SCRIPT_DIR/optimized" "pyflate_optimized"

echo
echo "############################################"
echo "# [4/4] Before/after comparison"
echo "############################################"
orig_s=$(parse_seconds "$SCRIPT_DIR/original/run_output_original.txt")
opt_s=$(parse_seconds "$SCRIPT_DIR/optimized/run_output_pyflate_optimized.txt")
python3 - "$orig_s" "$opt_s" <<'PYEOF'
import sys
orig, opt = float(sys.argv[1]), float(sys.argv[2])
pct = (orig - opt) / orig * 100
speedup = orig / opt
print(f"Original:    {orig:.4f} s")
print(f"Optimized:   {opt:.4f} s")
print(f"Improvement: {pct:.1f}% faster ({speedup:.1f}x speedup)")
print("Target (>= 7% improvement):", "PASS" if pct >= 7 else "FAIL")
PYEOF
echo
echo "See COMPARISON.md in this directory for the full write-up (timing,"
echo "perf stat hardware counters, self%-ranked hotspots, flame graphs)."
