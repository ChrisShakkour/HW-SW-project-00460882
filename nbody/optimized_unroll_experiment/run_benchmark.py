"""
N-body benchmark from the Computer Language Benchmarks Game.

Optimization attempt 4: manual loop unrolling.

The original advance()/report_energy() iterate over a precomputed `pairs`
list of 10 (body, body) tuples, re-destructuring each body's position out
of its list on every visit. Since each of the 5 bodies participates in 4
pairs, this re-reads/re-unpacks every body's position 4 times per
timestep. This version instead unpacks each body's position into local
scalars exactly once per timestep, then writes out all 10 pairwise force
computations and the 5 position updates explicitly (no `for pair in
pairs` loop, no per-pair tuple destructuring). This directly targets the
`_PyEval_EvalFrameDefault` interpreter-overhead hotspot (29% self-time,
the single largest cost in the profile) in a way the previous three
attempts (numpy vectorization, sqrt() vs **, flattened indices) did not.

Velocity lists (v0..v4) and mass scalars (m0..m4) are bound once outside
the loop since they're either mutated in place (velocities) or constant
(masses) — only positions need re-reading every timestep since they're
plain floats copied out of mutable lists.

Pulled from:
http://benchmarksgame.alioth.debian.org/u64q/program.php?test=nbody&lang=python3&id=1

Contributed by Kevin Carson.
Modified by Tupteq, Fredrik Johansson, and Daniel Nanz.
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


SYSTEM = list(BODIES.values())
PAIRS = combinations(SYSTEM)


def advance(dt, n, bodies=SYSTEM):
    (p0, v0, m0), (p1, v1, m1), (p2, v2, m2), (p3, v3, m3), (p4, v4, m4) = bodies

    for i in range(n):
        x0, y0, z0 = p0
        x1, y1, z1 = p1
        x2, y2, z2 = p2
        x3, y3, z3 = p3
        x4, y4, z4 = p4

        # pair (0, 1)
        dx = x0 - x1; dy = y0 - y1; dz = z0 - z1
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m0 * mag; b2m = m1 * mag
        v0[0] -= dx * b2m; v0[1] -= dy * b2m; v0[2] -= dz * b2m
        v1[0] += dx * b1m; v1[1] += dy * b1m; v1[2] += dz * b1m

        # pair (0, 2)
        dx = x0 - x2; dy = y0 - y2; dz = z0 - z2
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m0 * mag; b2m = m2 * mag
        v0[0] -= dx * b2m; v0[1] -= dy * b2m; v0[2] -= dz * b2m
        v2[0] += dx * b1m; v2[1] += dy * b1m; v2[2] += dz * b1m

        # pair (0, 3)
        dx = x0 - x3; dy = y0 - y3; dz = z0 - z3
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m0 * mag; b2m = m3 * mag
        v0[0] -= dx * b2m; v0[1] -= dy * b2m; v0[2] -= dz * b2m
        v3[0] += dx * b1m; v3[1] += dy * b1m; v3[2] += dz * b1m

        # pair (0, 4)
        dx = x0 - x4; dy = y0 - y4; dz = z0 - z4
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m0 * mag; b2m = m4 * mag
        v0[0] -= dx * b2m; v0[1] -= dy * b2m; v0[2] -= dz * b2m
        v4[0] += dx * b1m; v4[1] += dy * b1m; v4[2] += dz * b1m

        # pair (1, 2)
        dx = x1 - x2; dy = y1 - y2; dz = z1 - z2
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m1 * mag; b2m = m2 * mag
        v1[0] -= dx * b2m; v1[1] -= dy * b2m; v1[2] -= dz * b2m
        v2[0] += dx * b1m; v2[1] += dy * b1m; v2[2] += dz * b1m

        # pair (1, 3)
        dx = x1 - x3; dy = y1 - y3; dz = z1 - z3
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m1 * mag; b2m = m3 * mag
        v1[0] -= dx * b2m; v1[1] -= dy * b2m; v1[2] -= dz * b2m
        v3[0] += dx * b1m; v3[1] += dy * b1m; v3[2] += dz * b1m

        # pair (1, 4)
        dx = x1 - x4; dy = y1 - y4; dz = z1 - z4
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m1 * mag; b2m = m4 * mag
        v1[0] -= dx * b2m; v1[1] -= dy * b2m; v1[2] -= dz * b2m
        v4[0] += dx * b1m; v4[1] += dy * b1m; v4[2] += dz * b1m

        # pair (2, 3)
        dx = x2 - x3; dy = y2 - y3; dz = z2 - z3
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m2 * mag; b2m = m3 * mag
        v2[0] -= dx * b2m; v2[1] -= dy * b2m; v2[2] -= dz * b2m
        v3[0] += dx * b1m; v3[1] += dy * b1m; v3[2] += dz * b1m

        # pair (2, 4)
        dx = x2 - x4; dy = y2 - y4; dz = z2 - z4
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m2 * mag; b2m = m4 * mag
        v2[0] -= dx * b2m; v2[1] -= dy * b2m; v2[2] -= dz * b2m
        v4[0] += dx * b1m; v4[1] += dy * b1m; v4[2] += dz * b1m

        # pair (3, 4)
        dx = x3 - x4; dy = y3 - y4; dz = z3 - z4
        mag = dt * ((dx * dx + dy * dy + dz * dz) ** (-1.5))
        b1m = m3 * mag; b2m = m4 * mag
        v3[0] -= dx * b2m; v3[1] -= dy * b2m; v3[2] -= dz * b2m
        v4[0] += dx * b1m; v4[1] += dy * b1m; v4[2] += dz * b1m

        p0[0] += dt * v0[0]; p0[1] += dt * v0[1]; p0[2] += dt * v0[2]
        p1[0] += dt * v1[0]; p1[1] += dt * v1[1]; p1[2] += dt * v1[2]
        p2[0] += dt * v2[0]; p2[1] += dt * v2[1]; p2[2] += dt * v2[2]
        p3[0] += dt * v3[0]; p3[1] += dt * v3[1]; p3[2] += dt * v3[2]
        p4[0] += dt * v4[0]; p4[1] += dt * v4[1]; p4[2] += dt * v4[2]


def report_energy(bodies=SYSTEM, e=0.0):
    (p0, v0, m0), (p1, v1, m1), (p2, v2, m2), (p3, v3, m3), (p4, v4, m4) = bodies
    x0, y0, z0 = p0
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    x3, y3, z3 = p3
    x4, y4, z4 = p4

    dx = x0 - x1; dy = y0 - y1; dz = z0 - z1
    e -= (m0 * m1) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x0 - x2; dy = y0 - y2; dz = z0 - z2
    e -= (m0 * m2) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x0 - x3; dy = y0 - y3; dz = z0 - z3
    e -= (m0 * m3) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x0 - x4; dy = y0 - y4; dz = z0 - z4
    e -= (m0 * m4) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x1 - x2; dy = y1 - y2; dz = z1 - z2
    e -= (m1 * m2) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x1 - x3; dy = y1 - y3; dz = z1 - z3
    e -= (m1 * m3) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x1 - x4; dy = y1 - y4; dz = z1 - z4
    e -= (m1 * m4) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x2 - x3; dy = y2 - y3; dz = z2 - z3
    e -= (m2 * m3) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x2 - x4; dy = y2 - y4; dz = z2 - z4
    e -= (m2 * m4) / ((dx * dx + dy * dy + dz * dz) ** 0.5)
    dx = x3 - x4; dy = y3 - y4; dz = z3 - z4
    e -= (m3 * m4) / ((dx * dx + dy * dy + dz * dz) ** 0.5)

    for (r, [vx, vy, vz], m) in bodies:
        e += m * (vx * vx + vy * vy + vz * vz) / 2.
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
    runner.metadata['description'] = "n-body benchmark"
    runner.argparser.add_argument("--iterations",
                                  type=int, default=DEFAULT_ITERATIONS,
                                  help="Number of nbody advance() iterations "
                                       "(default: %s)" % DEFAULT_ITERATIONS)
    runner.argparser.add_argument("--reference",
                                  type=str, default=DEFAULT_REFERENCE,
                                  help="nbody reference (default: %s)"
                                       % DEFAULT_REFERENCE)

    args = runner.parse_args()
    runner.bench_time_func('nbody', bench_nbody,
                           args.reference, args.iterations)
