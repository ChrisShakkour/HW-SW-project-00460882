# HW-SW-project-00460882

HW/SW co-design project: profile, optimize, and propose a hardware
acceleration solution for two benchmarks from the `pyperformance` suite.

This README is written as a step-by-step guide — follow it top to bottom to
go from a blank environment to a finished submission.

## Project Roadmap

1. [Set up the environment](#1-set-up-the-environment)
2. [Choose your two benchmarks](#2-choose-your-two-benchmarks)
3. [Understand each benchmark](#3-understand-each-benchmark)
4. [Profile with perf](#4-profile-with-perf)
5. [Generate a flame graph](#5-generate-a-flame-graph)
6. [Detect bottlenecks](#6-detect-bottlenecks)
7. [Optimize the benchmark](#7-optimize-the-benchmark)
8. [Re-measure and compare (target: ≥7% on at least 2 benchmarks)](#8-re-measure-and-compare)
9. [Propose a hardware accelerator](#9-propose-a-hardware-accelerator)
10. [Repository structure & required deliverables](#10-repository-structure--required-deliverables)
11. [Prepare the presentation](#11-prepare-the-presentation)

---

## 1. Set Up the Environment

- Benchmarks run inside a QEMU/KVM Ubuntu 22.04 (jammy) VM.
- Profiling uses `perf` with Python debug symbols (`python3-dbg`) so internal
  CPython function calls are visible in the report.
- `pyperformance` runs the benchmarks themselves.

Check the tools are present in the VM:

```
which git perf python3 python3-dbg pip3
pip3 show pyperformance
```

If `pyperformance` is missing: `pip3 install pyperformance`.
If `python3-dbg` is missing: `apt-get update && apt-get install -y python3-dbg`.

Clone this repo inside the VM and do all work under it:

```
git clone git@github.com:ChrisShakkour/HW-SW-project-00460882.git
cd HW-SW-project-00460882
```

## 2. Choose Your Two Benchmarks

Pick **2** benchmarks from the approved list below (the numbering carries no
meaning — it's just an index into the assignment's table).

| Index | Name of Benchmark      |
|-------|-------------------------|
| 1     | Raytrace                |
| 2     | Deepcopy                |
| 3     | Mdp                     |
| 4     | Pathlib                 |
| 5     | Pickle and pickle_dict  |
| 6     | Pyflate                 |
| 7     | unpack_sequence         |
| 8     | tornado_http            |
| 9     | sqlite_synth            |
| 10    | Nbody                   |
| 11    | Btree                   |
| 12    | deepblue                |
| 13    | go                      |

Reference: [pyperformance Benchmark Documentation](https://pyperformance.readthedocs.io/benchmarks.html).

## 3. Understand Each Benchmark

For each of your two chosen benchmarks, before touching `perf`:

- Find its source under pyperformance's install path, e.g.:
  ```
  find / -iname "bm_<benchmark_name>*" 2>/dev/null
  ```
  (for example `bm_json_dumps/run_benchmark.py` for `json_dumps`).
- Read the benchmark script and note:
  - What operation it exercises (serialization, encryption, tree traversal,
    numeric simulation, etc.).
  - Which libraries and data structures it uses (stdlib, third-party, or
    custom).
  - The rough algorithmic shape of the hot path (loops, recursion, I/O).
- If it depends on a third-party library, skim that library's source for the
  specific code path the benchmark exercises — you'll need this understanding
  to justify your optimization and hardware proposal later.

Completed for both chosen benchmarks:
[pyflate/ANALYSIS.md](pyflate/ANALYSIS.md), [nbody/ANALYSIS.md](nbody/ANALYSIS.md).

## 4. Profile with perf

### Step 1 — Record with `perf`

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench <benchmark_name>
```

Replace `<benchmark_name>` with one of the approved benchmark names.

**Note on `-e cpu-clock`:** the plain command from the original course guide
(`perf record -F 999 -g -- python3-dbg -m pyperformance run --bench <name>`,
i.e. the default hardware `cycles` event) runs without error inside this VM
but silently records **zero samples** — `perf report` then fails with
`The perf.data data has no samples!`. This happens because frequency-based
sampling with a hardware event relies on a PMI (performance-monitoring
interrupt) to trigger each sample, and this QEMU/KVM guest's virtual PMU does
not reliably deliver that interrupt (plain counter reads via `perf stat -e
cycles` still work fine — only interrupt-driven sampling is affected).
Passing `-e cpu-clock` switches to a software, timer-based event that doesn't
depend on a hardware PMI, and reliably produces samples in this VM.

Example, for `json_dumps`:

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench json_dumps
```

### Step 2 — Export the report to text

```
perf report --stdio > perf_report_<name>.txt
```

Full example for `json_dumps`:

```
perf record -F 999 -g -e cpu-clock -- python3-dbg -m pyperformance run --bench json_dumps
perf report --stdio > perf_report_json_dumps.txt
```

Thanks to the Python debug symbols, the report reveals internal Python
function calls and stack traces (e.g. `_PyEval_EvalFrameDefault`,
`pymalloc_pool_extend`) used during the benchmark, which helps identify
performance bottlenecks within Python itself.

**Naming note:** raw perf output (call-graph report, `perf stat` counters,
software-event sampling report) is saved per-benchmark as
`perf_report_<name>.txt`, `perf_stat_<name>.txt`, and
`perf_report_<name>_swevents.txt` — these are reference/appendix material.
The assignment's required `report_<name_of_benchmark>.txt` is a different,
higher-level file: the full structured write-up (overview, analysis,
optimizations, comparison, HW proposal, conclusion) assembled in
[Section 10](#10-repository-structure--required-deliverables), which
references this raw data rather than being it.

## 5. Generate a Flame Graph

Flame graphs turn the same `perf.data` into a visual, interactive SVG.
Use Brendan Gregg's [FlameGraph](https://github.com/brendangregg/FlameGraph)
scripts:

```
git clone https://github.com/brendangregg/FlameGraph.git
perf script -i perf.data > out.perf
./FlameGraph/stackcollapse-perf.pl out.perf > out.folded
./FlameGraph/flamegraph.pl out.folded > flamegraph_<benchmark_name>.svg
```

Copy the resulting `.svg` out of the VM (e.g. `scp`) to view it in a browser.
Wider frames = more samples = hotter code paths — that's what you're
hunting for in the next step.

## 6. Detect Bottlenecks

Using `perf_report_<name>.txt` and the flame graph together:

- Identify the functions with the highest `Self` percentage — these are
  where the CPU is actually spending time (as opposed to `Children`, which
  includes time spent in callees).
- Look for wide, flat plateaus in the flame graph — repeated hot calls
  (e.g. allocator churn, a specific serialization routine, a heavy inner
  loop) are prime optimization targets.
- Distinguish bottlenecks that are:
  - **Algorithmic** (wrong data structure / big-O behavior),
  - **Library-related** (a slower pure-Python path vs. a faster C-accelerated
    one), or
  - **Memory/allocator-related** (excessive small allocations, GC pressure).
- Write down 1-2 concrete hotspots per benchmark — these become the targets
  for Section 7.

**Tip:** `perf_report_<name>.txt` is generated with call-graphs, which
nests each function under its *callers* and mixes `Self` into a
`Children`-sorted tree — useful for context, but hard to skim for pure
`Self`-time ranking. Re-run against the same `perf.data` (no need to
re-record) with:

```
perf report --stdio --no-children -g none -i perf.data
```

to get a flat table sorted purely by `Self` time.

Completed for both chosen benchmarks:
[pyflate/BOTTLENECKS.md](pyflate/BOTTLENECKS.md), [nbody/BOTTLENECKS.md](nbody/BOTTLENECKS.md).

## 7. Optimize the Benchmark

For each identified bottleneck, propose and implement a fix. Typical options:

- Swap in a more efficient library or built-in (e.g. a C-accelerated
  implementation instead of a pure-Python one).
- Replace an algorithm or data structure with a more efficient one.
- Reduce redundant work (caching, avoiding unnecessary copies, batching).

Keep the original benchmark code intact somewhere (e.g. a `before/` copy or a
git branch/tag) so you can run both versions for the comparison in the next
step.

**Measure, don't assume.** A hotspot in the profile doesn't guarantee its
"obvious" fix nets a real win — the replacement can carry its own
overhead (an extra function call, an extra allocation) that cancels out
or exceeds the savings. Always re-measure with a calibrated run rather
than trusting the theory; a rigorously-measured *negative* result is
still a valid, reportable finding (see `nbody`'s case below).

Completed for both chosen benchmarks:
[pyflate/OPTIMIZATIONS.md](pyflate/OPTIMIZATIONS.md) (a clear win — swap
in stdlib `bz2`), [nbody/OPTIMIZATIONS.md](nbody/OPTIMIZATIONS.md) (three
measured attempts, none beat the baseline — see its Conclusion for why,
and how that motivates Section 9's hardware proposal instead).

## 8. Re-measure and Compare

Re-run the same `perf record` / `perf report` commands from
[Section 4](#4-profile-with-perf) against your optimized code, and record the
`pyperformance` timing output (`Mean +- std dev`) from both the original and
optimized runs.

- Compute percentage improvement:
  `(original_mean - optimized_mean) / original_mean * 100`.
- **Target: at least 2 of your selected benchmarks show ≥7% improvement.**
  This is treated as sufficient by the assignment.
- Present a clear before/after table (timings + % improvement) for each
  benchmark in its `report_<name_of_benchmark>.txt`.

**Don't overwrite the baseline artifacts** when profiling the optimized
code — write the optimized run's `perf.data`/report/flame graph/stat
output into the `optimized/` subdirectory (or otherwise distinctly named),
so the original "before" profiling data stays intact for the comparison.

Completed: [pyflate/COMPARISON.md](pyflate/COMPARISON.md) — full
before/after `perf` comparison (timing, hardware counters, self%-ranked
hotspots) confirming both Section 6 bottlenecks are gone from the
optimized profile. `nbody`'s comparison doesn't need a separate write-up:
since none of its three optimization attempts beat the baseline (see
[nbody/OPTIMIZATIONS.md](nbody/OPTIMIZATIONS.md)), the baseline profiling
data already in this repo *is* the final comparison point.

## 9. Propose a Hardware Accelerator

For one or two key components identified as bottlenecks, design a hardware
accelerator. Example directions from the assignment: accelerating dictionary
operations, speeding up decompression, or an ISA extension for a
non-workload-specific operation (e.g. multiply-accumulate).

Your proposal must include:

- **Hardware description** — implemented in Verilog, SystemVerilog, or
  PyXHDL (other HDLs/frameworks need prior instructor approval). Doesn't need
  to be tapeout-ready, but must be a complete, logically consistent design.
- **Inputs and outputs** — data widths, interfaces, expected operating
  frequency.
- **Hardware architecture** — datapath and control logic.
- **Hardware/software interface** — how software talks to it: APIs, drivers,
  memory-mapped registers, DMA, or another communication protocol, plus any
  required software-side changes.
- **Acceleration justification** — why this component is a good candidate,
  an estimated performance improvement, and your assumptions.
- **Block diagram** — the accelerator, its interfaces, and its place in the
  overall system.
- **Performance/area/power trade-offs.**

You are not expected to synthesize, fabricate, or physically test the
hardware — but the design must be complete enough to fully define its
functionality, interfaces, frequency, and internal logic.

## 10. Repository Structure & Required Deliverables

For **each** of your two chosen benchmarks, this repo must contain:

- `report_<name_of_benchmark>.txt` — overview, initial analysis (including
  flame graphs / profiling data), optimizations made, before/after
  performance comparison, hardware acceleration proposal, and a conclusion.
  This is the structured write-up, distinct from the raw perf data below.
- `script_<name_of_benchmark>.sh` — environment setup, benchmark execution
  (perf + pyperformance), flame graph generation, and the post-optimization
  run with its comparison.
- Raw perf data backing the report above: `perf.data`, `perf_report_<name>.txt`
  (call-graph text report), `flamegraph_<name>.svg`, `perf_stat_<name>.txt`
  (hardware counters), and `perf_report_<name>_swevents.txt` (software-event
  sampling).

Plus, once per repo:

- Any additional supporting files (Python scripts, configs, HW source files,
  performance logs) — optional but encouraged.
- This `README.md`, explaining the repo layout and how to reproduce results.

Use clear, incremental commit messages that show your actual development
process — well-organized history and structure earns bonus points (+5).

## 11. Prepare the Presentation

- 20–25 minutes, at a time scheduled by course staff.
- Structure it as the natural flow of the work: analysis → profiling →
  bottlenecks → optimization → results → hardware proposal.
- Expect 5–10 minutes of questions — be ready to explain your choices.
- Have working code available to demo (no need to put all of it on slides).
- Aim the explanation at a fellow ECE student who didn't do the project —
  focus on teaching and demonstrating understanding, not just showing output.

