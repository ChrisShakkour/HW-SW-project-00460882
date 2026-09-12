"""
Optimized version of the nbody benchmark.

The original (see ../original/run_benchmark.py) computes the pairwise
gravitational force with plain Python floats and lists, and uses the
generic `**` power operator for `r ** -1.5` / `r ** 0.5` (see
../BOTTLENECKS.md for the profiling evidence: heavy float-object
boxing/deallocation, list item-assignment overhead, and a direct
`__ieee754_pow_fma` hotspot from the power operator).

This version:
  1. Replaces `r ** -1.5` / `r ** 0.5` with an equivalent
     `1 / (r2 * sqrt(r2))` / `sqrt(r2)` formulation (avoids the generic
     `pow()` call for a fixed, known exponent).
  2. Vectorizes the pairwise force computation and position/velocity
     updates with numpy arrays instead of per-body Python lists, so each
     timestep does a handful of array operations over all 10 pairs at
     once instead of ~60 individual Python-level scalar operations.

Physics and body data are identical to the original; only the numeric
representation and the power-operator formulation change (so exact
floating-point results may differ very slightly from the original due to
a different operation order, which is expected and immaterial to this
benchmark's own semantics — it doesn't checksum its output, only reports
wall-clock time for the same fixed workload).
"""

import numpy as np
import pyperf

__contact__ = "collinwinter@google.com (Collin Winter)"
DEFAULT_ITERATIONS = 20000
DEFAULT_REFERENCE = 'sun'

PI = 3.14159265358979323
SOLAR_MASS = 4 * PI * PI
DAYS_PER_YEAR = 365.24

BODY_NAMES = ['sun', 'jupiter', 'saturn', 'uranus', 'neptune']

BODIES = {
    'sun': ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], SOLAR_MASS),

    'jupiter': ([4.84143144246472090e+00,
                 -1.16032004402742839e+00,
                 -1.03622044471123109e-01],
                [1.66007664274403694e-03 * DAYS_PER_YEAR,
                 7.69901118419740425e-03 * DAYS_PER_YEAR,
                 -6.90460016972063023e-05 * DAYS_PER_YEAR],
                9.54791938424326609e-04 * SOLAR_MASS),

    'saturn': ([8.34336671824457987e+00,
                4.12479856412430479e+00,
                -4.03523417114321381e-01],
               [-2.76742510726862411e-03 * DAYS_PER_YEAR,
                4.99852801234917238e-03 * DAYS_PER_YEAR,
                2.30417297573763929e-05 * DAYS_PER_YEAR],
               2.85885980666130812e-04 * SOLAR_MASS),

    'uranus': ([1.28943695621391310e+01,
                -1.51111514016986312e+01,
                -2.23307578892655734e-01],
               [2.96460137564761618e-03 * DAYS_PER_YEAR,
                2.37847173959480950e-03 * DAYS_PER_YEAR,
                -2.96589568540237556e-05 * DAYS_PER_YEAR],
               4.36624404335156298e-05 * SOLAR_MASS),

    'neptune': ([1.53796971148509165e+01,
                 -2.59193146099879641e+01,
                 1.79258772950371181e-01],
                [2.68067772490389322e-03 * DAYS_PER_YEAR,
                 1.62824170038242295e-03 * DAYS_PER_YEAR,
                 -9.51592254519715870e-05 * DAYS_PER_YEAR],
                5.15138902046611451e-05 * SOLAR_MASS)}

# Pair indices for the 10 unique unordered pairs among 5 bodies.
_PAIR_I, _PAIR_J = zip(*[(i, j) for i in range(5) for j in range(i + 1, 5)])
PAIR_I = np.array(_PAIR_I)
PAIR_J = np.array(_PAIR_J)


def make_system():
    pos = np.array([BODIES[name][0] for name in BODY_NAMES], dtype=np.float64)
    vel = np.array([BODIES[name][1] for name in BODY_NAMES], dtype=np.float64)
    mass = np.array([BODIES[name][2] for name in BODY_NAMES], dtype=np.float64)
    return pos, vel, mass


def offset_momentum(vel, mass, reference_index):
    p = -(vel * mass[:, None]).sum(axis=0)
    vel[reference_index] = p / mass[reference_index]


def advance(dt, n, pos, vel, mass):
    m_i = mass[PAIR_I]
    m_j = mass[PAIR_J]
    for _ in range(n):
        d = pos[PAIR_I] - pos[PAIR_J]
        r2 = (d * d).sum(axis=1)
        mag = dt / (r2 * np.sqrt(r2))
        # Newton's third law: body I's velocity update uses body J's mass
        # (and vice versa) — matches the original's `v1 -= dx*b2m`,
        # `v2 += dx*b1m` where b1m/b2m are scaled by the *other* body's mass.
        np.subtract.at(vel, PAIR_I, d * (m_j * mag)[:, None])
        np.add.at(vel, PAIR_J, d * (m_i * mag)[:, None])
        pos += dt * vel


def report_energy(pos, vel, mass):
    d = pos[PAIR_I] - pos[PAIR_J]
    r2 = (d * d).sum(axis=1)
    e = -(mass[PAIR_I] * mass[PAIR_J] / np.sqrt(r2)).sum()
    e += (mass * (vel * vel).sum(axis=1)).sum() / 2.0
    return e


def bench_nbody(loops, reference, iterations):
    pos, vel, mass = make_system()
    offset_momentum(vel, mass, BODY_NAMES.index(reference))

    range_it = range(loops)
    t0 = pyperf.perf_counter()

    for _ in range_it:
        report_energy(pos, vel, mass)
        advance(0.01, iterations, pos, vel, mass)
        report_energy(pos, vel, mass)

    return pyperf.perf_counter() - t0


def add_cmdline_args(cmd, args):
    cmd.extend(("--iterations", str(args.iterations)))


if __name__ == '__main__':
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "n-body benchmark (optimized: numpy + sqrt)"
    runner.argparser.add_argument("--iterations",
                                  type=int, default=DEFAULT_ITERATIONS,
                                  help="Number of nbody advance() iterations "
                                       "(default: %s)" % DEFAULT_ITERATIONS)
    runner.argparser.add_argument("--reference",
                                  type=str, default=DEFAULT_REFERENCE,
                                  help="nbody reference (default: %s)"
                                       % DEFAULT_REFERENCE)

    args = runner.parse_args()
    runner.bench_time_func('nbody_optimized', bench_nbody,
                           args.reference, args.iterations)

