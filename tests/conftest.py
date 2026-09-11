"""Фикстуры, общие для всего набора тестов."""

from __future__ import annotations

import pytest

from cosmo_net.config import SCENARIOS_DIR
from cosmo_net.scenario.io import load_scenario
from cosmo_net.scenario.schema import Scenario


@pytest.fixture(scope="session")
def full_constellation() -> Scenario:
    return load_scenario(SCENARIOS_DIR / "01_full_constellation.json")


@pytest.fixture(scope="session")
def first_launch() -> Scenario:
    return load_scenario(SCENARIOS_DIR / "02_first_launch.json")


@pytest.fixture(scope="session")
def satellite_outages() -> Scenario:
    return load_scenario(SCENARIOS_DIR / "03_satellite_outages.json")


@pytest.fixture(scope="session")
def link_range() -> Scenario:
    return load_scenario(SCENARIOS_DIR / "04_link_range.json")
