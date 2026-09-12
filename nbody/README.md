# nbody — Profiling Artifacts

This directory holds the baseline profiling data for the `nbody` benchmark
(a gravitational N-body simulation from `pyperformance`).

Baseline result: `nbody: Mean +- std dev: 483 ms +- 4 ms`

## Files

- `perf.data` — raw perf sample data from the call-graph (`cpu-clock`) run.
- `report_nbody.txt` — call-graph text report generated from `perf.data`.
- `flamegraph_nbody.svg` — interactive flame graph generated from the same
  run (open in a browser).
- `perf_stat_nbody.txt` — aggregate hardware performance-counter stats
  (cycles, instructions, cache, branches) for the whole benchmark run.
- `report_nbody_swevents.txt` — call-graph report sampled on software
  events (page faults, minor faults, context switches, CPU migrations),
  generated from `perf_swevents.data` (not committed — see below).
- `venv/` — pyperformance's per-benchmark virtual environment (git-ignored).

Note: `perf_swevents.data` itself is **not** committed to keep the repo
lightweight — only the derived `report_nbody_swevents.txt` is kept.
Regenerate the raw data locally with the command below if needed.

## Commands Used

Run from inside this directory (`HW-SW-project-00460882/nbody`).

### 1. Call-graph recording with perf

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench nbody
perf report --stdio > report_nbody.txt
```

`-e cpu-clock` is required instead of the default hardware `cycles` event —
see the top-level [README.md](../README.md#4-profile-with-perf) for why
(this VM's virtual PMU doesn't deliver the performance-monitoring interrupt
that hardware-event sampling relies on, so `cycles` silently records zero
samples here).

### 2. Flame graph

Uses the shared [FlameGraph](https://github.com/brendangregg/FlameGraph)
scripts cloned once into `../tools/FlameGraph`:

```
perf script -i perf.data > out.perf
../tools/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
../tools/FlameGraph/flamegraph.pl out.folded > flamegraph_nbody.svg
```

### 3. Hardware counter stats (`perf stat`)

Aggregate counts (not sampled) for the whole run — cycles, instructions
(→ IPC), cache references/misses, branch instructions/misses, bus cycles,
reference cycles. Unlike `perf record`, `perf stat` reads counters directly
and works reliably in this VM even for hardware events:

```
perf stat -e cycles,instructions,cache-references,cache-misses,branch-instructions,branch-misses,bus-cycles,ref-cycles \
  -- python3-dbg -m pyperformance run --bench nbody > /dev/null 2> perf_stat_nbody.txt
```

Result summary: **2.32 insn/cycle**, **3.3% cache-miss rate**, **0.55%
branch-miss rate** — better cache locality than pyflate, consistent with
nbody's regular array-of-floats access pattern in its pairwise force loop.

### 4. Software-event sampling

Samples on discrete software-tracked events instead of a hardware/timer
event — useful for seeing *where* faults, context switches, or migrations
happen, since these aren't tied to hardware PMI support:

```
perf record -c 1 -g -e page-faults,minor-faults,context-switches,cpu-migrations \
  -o perf_swevents.data -- python3-dbg -m pyperformance run --bench nbody
perf report --stdio -i perf_swevents.data > report_nbody_swevents.txt
```

`-c 1` samples every occurrence (these events are rare/discrete, unlike
frequency-based cycle/clock sampling). Result: ~149K page-fault/minor-fault
samples, 495 context-switch samples, zero cpu-migrations (the run was short
enough / stable enough on its vCPU that no migration occurred).

## Reproduce End-to-End

```
cd HW-SW-project-00460882/nbody

# call graph + flame graph
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench nbody
perf report --stdio > report_nbody.txt
perf script -i perf.data > out.perf
../tools/FlameGraph/stackcollapse-perf.pl out.perf > out.folded
../tools/FlameGraph/flamegraph.pl out.folded > flamegraph_nbody.svg

# hardware counter stats
perf stat -e cycles,instructions,cache-references,cache-misses,branch-instructions,branch-misses,bus-cycles,ref-cycles \
  -- python3-dbg -m pyperformance run --bench nbody > /dev/null 2> perf_stat_nbody.txt

# software-event sampling
perf record -c 1 -g -e page-faults,minor-faults,context-switches,cpu-migrations \
  -o perf_swevents.data -- python3-dbg -m pyperformance run --bench nbody
perf report --stdio -i perf_swevents.data > report_nbody_swevents.txt
```

