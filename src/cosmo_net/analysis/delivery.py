"""
Доставка с допустимой задержкой: когда данные дойдут, если разрешить их подождать.

Постановка спрашивает, есть ли сквозной путь **прямо сейчас**. Для телефонного
разговора это верный вопрос, для телеметрии и файлов — нет: аппарат может забрать
данные над пунктом, увезти их на борту и сбросить над шлюзом позже. Приём называется
хранением с переносом и в космической связи стандартен; расчёт по заранее известному
расписанию контактов — это маршрутизация по графу контактов.

Здесь считается ровно одна величина: через сколько секунд после появления данные
окажутся у шлюза, если пользоваться и переносом на борту, и ожиданием на самом
терминале. Мгновенная доступность — это частный случай, доля отсчётов с нулевой
задержкой, и она обязана совпасть с тем, что считает `reachability`. Так это и
проверяется тестом: совпадает точно на всех четырёх выданных сценариях.

Измерено на сценариях 03 и 04: при нулевой задержке худший пункт даёт 79.3 % и
62.2 %, при допустимых пятнадцати минутах — 98.6 % и 100 %, а самая долгая доставка
за сутки занимает 20 и 14 минут. То есть эти два сценария не провалены вообще —
они провалены только для трафика реального времени.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cosmo_net.analysis.reachability import connected_components
from cosmo_net.geometry.contacts import ContactSeries, compute_contacts, gateway_offline_mask
from cosmo_net.geometry.orbit import compute_trajectory
from cosmo_net.scenario.schema import Scenario

# Пороги задержки, по которым отчёт даёт разрез. Ноль обязателен: это мгновенная
# доступность, и именно на нём расчёт сверяется с основным. Остальные выбраны так,
# чтобы накрыть интересный диапазон — на выданных данных всё заканчивается на
# двадцати минутах, кроме первой очереди, которая не заканчивается никогда.
DEADLINES_S: tuple[int, ...] = (0, 300, 900, 1800, 3600, 7200)


@dataclass(frozen=True)
class ClientDelivery:
    """Задержки доставки одного пункта за весь горизонт."""

    client_id: str

    latency_s: np.ndarray
    """(T,) секунды до доставки данных, появившихся на этом отсчёте. `inf` — не доставлены."""

    def share_within(self, deadline_s: float) -> float:
        """Доля отсчётов, данные с которых успевают дойти за отведённое время."""

        if self.latency_s.size == 0:
            return 0.0
        return float((self.latency_s <= deadline_s).mean())

    @property
    def undelivered_share(self) -> float:
        """Доля отсчётов, данные с которых не доходят до конца горизонта вообще."""

        if self.latency_s.size == 0:
            return 0.0
        return float((~np.isfinite(self.latency_s)).mean())

    @property
    def max_latency_s(self) -> float | None:
        """Самая долгая доставка из тех, что состоялись. `None`, если не состоялась ни одна."""

        finite = self.latency_s[np.isfinite(self.latency_s)]
        return float(finite.max()) if finite.size else None

    def summary(self, deadlines_s: tuple[int, ...] = DEADLINES_S) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "undelivered_share": self.undelivered_share,
            "max_latency_s": self.max_latency_s,
            "within": [
                {"deadline_s": d, "share": self.share_within(d)} for d in deadlines_s
            ],
        }


@dataclass
class DeliveryReport:
    """Что даёт разрешение подождать, по каждому пункту сразу."""

    deadlines_s: tuple[int, ...]
    clients: list[ClientDelivery] = field(default_factory=list)

    def worst_share_within(self, deadline_s: float) -> float:
        """
        Доля у пункта, обслуженного хуже всех при этой допустимой задержке.

        Пункт, обслуженный хуже всех, на разных порогах может быть разным, и это не
        ошибка: цель задана на каждый терминал, поэтому и здесь судить надо по
        минимуму, а не по среднему.
        """

        if not self.clients:
            return 0.0
        return min(c.share_within(deadline_s) for c in self.clients)

    @property
    def worst_max_latency_s(self) -> float | None:
        values = [c.max_latency_s for c in self.clients if c.max_latency_s is not None]
        return max(values) if values else None

    def to_dict(self) -> dict[str, object]:
        return {
            "deadlines_s": list(self.deadlines_s),
            "worst_within": [
                {"deadline_s": d, "share": self.worst_share_within(d)}
                for d in self.deadlines_s
            ],
            "worst_max_latency_s": self.worst_max_latency_s,
            "clients": [c.summary(self.deadlines_s) for c in self.clients],
        }


def earliest_delivery_times(
    scenario: Scenario, contacts: ContactSeries | None = None
) -> tuple[np.ndarray, ContactSeries]:
    """
    (T, N) самое раннее время, когда данные, лежащие на аппарате, окажутся у шлюза.

    Считается одним обратным проходом по сетке. На каждом отсчёте у аппарата три
    возможности, и берётся лучшая из них: сбросить шлюзу прямо сейчас, подождать
    следующего отсчёта на борту, либо передать по межспутниковой связи. Последняя
    возможность внутри отсчёта означает, что вся связная компонента получает общий
    минимум, — то есть считать её надо не перебором путей, а тем же объединением
    множеств, которым уже считается достижимость.

    Стоимость — один проход по отсчётам вместо поиска из каждой точки в каждую:
    десятки миллисекунд на весь горизонт против минут у наивного способа.
    """

    if contacts is None:
        contacts = compute_contacts(scenario, compute_trajectory(scenario))

    times = np.asarray(contacts.times_s, dtype=float)
    offline = gateway_offline_mask(scenario, times)
    steps = len(times)
    count = len(contacts.satellite_ids)

    gateway_slots = [g for g, site in enumerate(scenario.ground_sites) if site.role == "gateway"]

    earliest = np.full((steps, count), np.inf, dtype=float)
    for step in range(steps - 1, -1, -1):
        best = np.full(count, np.inf, dtype=float)

        # Сбросить шлюзу на этом же отсчёте. Станция в отказе данные не принимает.
        for g in gateway_slots:
            if offline[step, g]:
                continue
            visible = np.flatnonzero(contacts.ground_open[step, g])
            best[visible] = np.minimum(best[visible], times[step])

        # Подождать на борту: то, что этот аппарат сможет сделать на следующем отсчёте.
        if step + 1 < steps:
            best = np.minimum(best, earliest[step + 1])

        # Передать по сети: связь внутри отсчёта мгновенна на масштабе шага, поэтому
        # вся компонента связности пользуется лучшим результатом любого своего члена.
        labels = connected_components(contacts.isl_open[step], contacts.pair_index, count)
        order = np.argsort(labels, kind="stable")
        grouped = labels[order]
        boundaries = np.flatnonzero(np.diff(grouped)) + 1
        for chunk in np.split(order, boundaries):
            best[chunk] = best[chunk].min()

        earliest[step] = best

    return earliest, contacts


def delivery_latency(
    scenario: Scenario, contacts: ContactSeries | None = None
) -> list[ClientDelivery]:
    """
    Для каждого пункта — задержка доставки данных, появившихся на каждом отсчёте.

    К переносу на борту добавляется ожидание на самом терминале: данные, для которых
    прямо сейчас нет подходящего аппарата, лежат в пункте до ближайшего сеанса.
    Технически это накопительный минимум с конца горизонта.
    """

    earliest, contacts = earliest_delivery_times(scenario, contacts)
    times = np.asarray(contacts.times_s, dtype=float)
    steps = len(times)

    result: list[ClientDelivery] = []
    for g, site in enumerate(scenario.ground_sites):
        if site.role != "client":
            continue

        delivered = np.full(steps, np.inf, dtype=float)
        for step in range(steps):
            visible = np.flatnonzero(contacts.ground_open[step, g])
            if visible.size:
                delivered[step] = earliest[step][visible].min()

        # Ожидание на терминале: подождать можно, вернуться в прошлое нельзя.
        delivered = np.minimum.accumulate(delivered[::-1])[::-1]

        result.append(ClientDelivery(client_id=site.id, latency_s=delivered - times))
    return result


def delivery_report(
    scenario: Scenario,
    contacts: ContactSeries | None = None,
    deadlines_s: tuple[int, ...] = DEADLINES_S,
) -> DeliveryReport:
    """Собрать разрез по порогам допустимой задержки для всех пунктов сразу."""

    return DeliveryReport(
        deadlines_s=deadlines_s, clients=delivery_latency(scenario, contacts)
    )
