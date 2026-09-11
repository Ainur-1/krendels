"""
Сколько от этой машины процессу на самом деле позволено занять.

Внутри контейнера `os.cpu_count()` отвечает не на тот вопрос: он показывает ядра
хоста, а не квоту cgroup, в которую процесс заключён. На инстансе с 512 МБ памяти
это привело к восьми рабочим процессам, каждый со своим numpy и своей копией
прогона, и ядро убило сервис — подбор конфигурации утащил за собой всё, включая
кеш посчитанных прогонов.

Поэтому ограничения читаются из cgroup, если она есть, и связывающим ограничением
оказывается память, а не ядра: процесс дёшев по процессору и дорог по занятым
страницам. Переоценить число ядер — значит получить медленно, переоценить память —
значит не получить ничего.
"""

from __future__ import annotations

import os
from pathlib import Path

# Память одного рабочего процесса: интерпретатор, numpy и массивы одного прогона.
# Массивы — это таблицы межспутниковых связей размера (T, P), около 26 МБ на
# базовом сценарии; остальное приходится на импорт. Округлено вверх намеренно:
# ошибка в большую сторону стоит одного процесса, в меньшую — всего сервиса.
WORKER_MEMORY_MB = 220

# Оставлено родительскому процессу, HTTP-серверу и файловому кешу.
RESERVED_MEMORY_MB = 180

CGROUP_V2 = Path("/sys/fs/cgroup")


def available_cpus() -> int:
    """Ядра, на которых процессу разрешено выполняться, с учётом квоты cgroup, если она задана."""

    try:
        cpus = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1

    quota = _cpu_quota()
    if quota is not None:
        cpus = min(cpus, quota)
    return max(1, cpus)


def available_memory_mb() -> int | None:
    """Потолок памяти из cgroup в мегабайтах либо None, если процесс не ограничен."""

    for name in ("memory.max", "memory/memory.limit_in_bytes"):
        text = _read(CGROUP_V2 / name)
        if text is None or text == "max":
            continue
        try:
            value = int(text)
        except ValueError:
            continue
        # Неограниченная cgroup сообщает число порядка 2^63; всё, что больше
        # терабайта, — это такая заглушка, а не настоящее ограничение.
        if 0 < value < 1 << 40:
            return value // (1024 * 1024)
    return None


def usable_workers(requested: int | None = None) -> int:
    """
    Сколько процессов запускать параллельно, с ограничением по тому, что вмещает контейнер.

    Явно запрошенное значение `requested` тоже ограничивается: тот, кто просит восемь
    процессов на инстансе с 512 МБ, просит убить сервис, и выполнить такую просьбу
    буквально — не значит уважить его намерение.
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
    """Разрешённое cgroup число целых ядер, округлённое вверх: 0.5 ядра всё равно даёт одно."""

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
