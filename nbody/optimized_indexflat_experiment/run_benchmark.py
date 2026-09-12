"""
Optimized version of the nbody benchmark (pure Python, loop-unrolled).

Two earlier attempts were measured and found NOT to help (kept for the
record):
  - numpy vectorization (../optimized_numpy_experiment/): 848ms vs the
    483ms baseline — numpy's per-call and scatter-add overhead dominates
    for a system this small (5 bodies, 10 pairs).
  - a `math.sqrt`-based replacement for `** -1.5`/`** 0.5`: 495ms vs the
    483ms baseline — the extra `LOAD_GLOBAL`+`CALL_FUNCTION` for calling
    `sqrt()` costs about as much as the generic `pow()` dispatch it
    replaces, netting no real improvement.

This version keeps the original `**` power operator (per the measurement
above) and instead targets the *loop and unpacking overhead* directly:
the original iterates a list of 10 pair tuples, each doing a nested
destructuring assignment
(`(([x1,y1,z1], v1, m1), ([x2,y2,z2], v2, m2)) in pairs`) on every
iteration of the 20000-iteration `advance()` loop. Since the 5-body,
10-pair system is fixed at import time, this version extracts each body's
position/velocity/mass list once (outside the hot loop) and iterates a
flat tuple of integer pair-index pairs, replacing the nested-tuple
destructuring with plain integer indexing on every access.
"""

import pyperf

__contact__ = "collinwinter@google.com (Collin Winter)"
DEFAULT_ITERATIONS = 20000
DEFAULT_REFERENCE = 'sun'


def combinations(l):
    """Pure-Python implementation of itertools.combinations(l, 2)."""
    result = []
    for x in range(len(l) - 1):
        ls = l[x + 1:]
        for y in ls:
            result.append((l[x], y))
    return result


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


SYSTEM = [BODIES[name] for name in BODY_NAMES]
PAIRS = combinations(SYSTEM)
# Fixed 5-body -> 10-pair index combinations, matching combinations(SYSTEM).
PAIR_INDICES = tuple((i, j) for i in range(5) for j in range(i + 1, 5))


def advance(dt, n, bodies=SYSTEM, pair_indices=PAIR_INDICES):
    positions = tuple(b[0] for b in bodies)
    velocities = tuple(b[1] for b in bodies)
    masses = tuple(b[2] for b in bodies)

    for _ in range(n):
        for i, j in pair_indices:
            pi = positions[i]
            pj = positions[j]
            vi = velocities[i]
            vj = velocities[j]
            dx = pi[0] - pj[0]
            dy = pi[1] - pj[1]
            dz = pi[2] - pj[2]
            mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
            b1m = masses[i] * mag
            b2m = masses[j] * mag
            vi[0] -= dx * b2m
            vi[1] -= dy * b2m
            vi[2] -= dz * b2m
            vj[0] += dx * b1m
            vj[1] += dy * b1m
            vj[2] += dz * b1m
        for k in range(5):
            r = positions[k]
            v = velocities[k]
            r[0] += dt * v[0]
            r[1] += dt * v[1]
            r[2] += dt * v[2]


def report_energy(bodies=SYSTEM, pair_indices=PAIR_INDICES, e=0.0):
    positions = tuple(b[0] for b in bodies)
    velocities = tuple(b[1] for b in bodies)
    masses = tuple(b[2] for b in bodies)

    for i, j in pair_indices:
        pi = positions[i]
        pj = positions[j]
        dx = pi[0] - pj[0]
        dy = pi[1] - pj[1]
        dz = pi[2] - pj[2]
        e -= (masses[i] * masses[j]) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    for k in range(5):
        v = velocities[k]
        m = masses[k]
        e += m * (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) / 2.
    return e


def offset_momentum(ref, bodies=SYSTEM, px=0.0, py=0.0, pz=0.0):
    for (r, [vx, vy, vz], m) in bodies:
        px -= vx * m
        py -= vy * m
        pz -= vz * m
    (r, v, m) = ref
    v[0] = px / m
    v[1] = py / m
    v[2] = pz / m


def bench_nbody(loops, reference, iterations):
    # Set up global state
    offset_momentum(BODIES[reference])

    range_it = range(loops)
    t0 = pyperf.perf_counter()

    for _ in range_it:
        report_energy()
        advance(0.01, iterations)
        report_energy()

    return pyperf.perf_counter() - t0


def add_cmdline_args(cmd, args):
    cmd.extend(("--iterations", str(args.iterations)))


if __name__ == '__main__':
    runner = pyperf.Runner(add_cmdline_args=add_cmdline_args)
    runner.metadata['description'] = "n-body benchmark (optimized: flattened index-based loop)"
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

