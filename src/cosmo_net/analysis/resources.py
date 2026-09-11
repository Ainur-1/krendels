"""
How much of this machine the process may actually use.

`os.cpu_count()` is the wrong question inside a container: it reports the cores of
the host, not the quota of the cgroup the process is confined to. Asking it on a
512 MB instance produced eight worker processes, each loading its own NumPy and its
own copy of the run, and the kernel killed the service — the configuration sweep
took the whole thing down and every cached run with it.

So the limits are read from the cgroup where there is one, and memory is the binding
constraint rather than cores: a worker is cheap in CPU and expensive in resident
pages, and overcommitting cores merely makes things slow while overcommitting memory
makes them stop.
"""

from __future__ import annotations

import os
from pathlib import Path

# Resident set of one worker: the interpreter, NumPy, and one run's arrays. The run
# arrays are the (T, P) inter-satellite tables, about 26 MB for a default scenario;
# the rest is import overhead. Rounded up, because being wrong upwards costs a
# worker and being wrong downwards costs the service.
WORKER_MEMORY_MB = 220

# Left for the parent process, the HTTP server and the page cache.
RESERVED_MEMORY_MB = 180

CGROUP_V2 = Path("/sys/fs/cgroup")


def available_cpus() -> int:
    """Cores this process may schedule on, honouring cgroup quota where one is set."""

    try:
        cpus = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1

    quota = _cpu_quota()
    if quota is not None:
        cpus = min(cpus, quota)
    return max(1, cpus)


def available_memory_mb() -> int | None:
    """The cgroup memory ceiling in MB, or None when the process is not limited."""

    for name in ("memory.max", "memory/memory.limit_in_bytes"):
        text = _read(CGROUP_V2 / name)
        if text is None or text == "max":
            continue
        try:
            value = int(text)
        except ValueError:
            continue
        # An unlimited cgroup reports a number close to 2^63; anything above a
        # terabyte is that sentinel rather than a real limit.
        if 0 < value < 1 << 40:
            return value // (1024 * 1024)
    return None


def usable_workers(requested: int | None = None) -> int:
    """
    How many processes to run in parallel, capped by what the container can hold.

    An explicit `requested` is still capped: a caller asking for eight on a 512 MB
    instance is asking for the service to be killed, and honouring that is not
    respectful of their intent.
    """

    limit = available_cpus()

    memory = available_memory_mb()
    if memory is not None:
        affordable = (memory - RESERVED_MEMORY_MB) // WORKER_MEMORY_MB
        limit = min(limit, max(1, affordable))

    if requested is not None:
        limit = min(limit, max(1, requested))
    return max(1, limit)


def _cpu_quota() -> int | None:
    """Whole cores allowed by the cgroup, rounded up so a 0.5 CPU share still gets one."""

    text = _read(CGROUP_V2 / "cpu.max")
    if text:
        parts = text.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if period > 0:
                    return max(1, -(-quota // period))
            except ValueError:
                pass

    quota_text = _read(CGROUP_V2 / "cpu/cpu.cfs_quota_us")
    period_text = _read(CGROUP_V2 / "cpu/cpu.cfs_period_us")
    if quota_text and period_text:
        try:
            quota, period = int(quota_text), int(period_text)
            if quota > 0 and period > 0:
                return max(1, -(-quota // period))
        except ValueError:
            pass
    return None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
