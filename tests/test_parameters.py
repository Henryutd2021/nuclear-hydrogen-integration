import math

import numpy as np
import pytest

from nuclear_h2 import Market, ParameterError, Service, iso_services
from nuclear_h2.parameters import merge_collinear
from nuclear_h2.services import ISO_PRODUCTS

from conftest import battery, electrolyzer, market, plant, storage, turbine


def test_valid_plant_passes():
    plant(battery=battery(), storage=storage()).validate()


def test_turbine_curve_and_heat_to_power():
    tb = turbine()
    assert tb.power_at(350.0) == pytest.approx(125.0)
    assert tb.power_at(500.0) == pytest.approx(125.0 + 150.0 / 170.0 * 60.0)
    assert tb.heat_to_power == pytest.approx(tb.power_at(500.0) / 500.0)


def test_merge_collinear_keeps_the_curve():
    x = np.array([0.0, 1.0, 2.0, 3.0])
    y = np.array([0.0, 2.0, 4.0, 5.0])
    xm, ym = merge_collinear(x, y)
    assert list(xm) == [0.0, 2.0, 3.0]
    assert np.allclose(np.interp(x, xm, ym), y)


@pytest.mark.parametrize("kw", [dict(steam_total_mwth=600.0), dict(p_min_mw=200.0),
                                dict(steam_breakpoints_mwth=(150.0, 140.0, 520.0)), dict(ramp_up_mw_per_h=-1.0),
                                dict(power_breakpoints_mw=(50.0, 125.0))])
def test_turbine_validation(kw):
    with pytest.raises(ParameterError):
        turbine(**kw).validate()


def test_electrolyzer_breakpoints():
    el = electrolyzer()
    prod = el.production_breakpoints_kg_per_h()
    assert prod == pytest.approx([8000 / 40.0, 20000 / 39.0, 40000 / 38.5])
    assert el.steam_breakpoints_mwth() == pytest.approx(prod * 7.0 / 1000.0)
    assert el.production_at([0.0, 8.0, 30.0])[0] == 0.0
    assert el.production_at(30.0) == pytest.approx(np.interp(30.0, [8, 20, 40], prod))
    assert el.steam_at(0.0) == 0.0


@pytest.mark.parametrize("kw", [dict(min_on_mw=6.0), dict(capacity_mw=45.0), dict(capacity_mw=5.0),
                                dict(electricity_kwh_per_kg=(40.0, 39.0)), dict(heat_kwh_per_kg=(7.0, -1.0, 7.0)),
                                dict(power_breakpoints_mw=(0.0, 20.0, 40.0)), dict(min_up_h=0),
                                dict(electricity_kwh_per_kg=(40.0, 120.0, 38.5)), dict(target_capacity_factor=1.2),
                                dict(capacity_mw=None, capacity_range_mw=(2.0, 55.0)),
                                dict(capacity_mw=None), dict(capacity_range_mw=(2.0, 30.0)),
                                dict(capacity_mw=None, capacity_range_mw=(9.0, 5.0)), dict(initial_state_h=0.0)])
def test_electrolyzer_validation(kw):
    with pytest.raises(ParameterError):
        electrolyzer(**kw).validate()


def test_sized_electrolyzer_upper_bound():
    el = electrolyzer(capacity_mw=None, capacity_range_mw=(6.0, 30.0))
    el.validate()
    assert el.sized and el.upper_mw == 30.0 and el.lower_mw == 6.0
    assert not electrolyzer().sized and electrolyzer().upper_mw == electrolyzer().lower_mw == 40.0


@pytest.mark.parametrize("kw", [dict(charge_efficiency=0.0), dict(soc_min_fraction=0.4), dict(end_soc_fraction=0.05),
                                dict(energy_mwh=None), dict(energy_range_mwh=(1.0, 9.0)), dict(power_per_energy=0.0)])
def test_battery_validation(kw):
    with pytest.raises(ParameterError):
        battery(**kw).validate()


@pytest.mark.parametrize("kw", [dict(initial_kg=100.0), dict(capacity_kg=None),
                                dict(initial_kg=7_000.0), dict(discharge_efficiency=1.3)])
def test_storage_validation(kw):
    with pytest.raises(ParameterError):
        storage(**kw).validate()


def test_storage_needs_electrolyzer():
    with pytest.raises(ParameterError):
        plant(electrolyzer=None, storage=storage()).validate()


def test_service_sign_and_column():
    up, down = Service("A", "up"), Service("B", "down", product="X")
    assert (up.sign, down.sign) == (1.0, -1.0)
    assert down.column == "X" and up.column == "A"
    assert Service("C", "down", energy_sign=1.0).sign == 1.0


@pytest.mark.parametrize("services", [
    (Service("A", "up"), Service("A", "down")),
    (Service("A", "up", balance_with="Z"),),
    (Service("A", "up", balance_with="B"), Service("B", "up", balance_with="A")),
    (Service("A", "sideways"),),
    (Service("A", "up", components=("reactor",)),),
])
def test_market_service_validation(services):
    with pytest.raises(ParameterError):
        market(services=services).validate()


def test_market_mode_and_limits():
    with pytest.raises(ParameterError):
        market(as_mode="realtime").validate()
    with pytest.raises(ParameterError):
        market(import_max_mw=-1.0).validate()
    assert math.isinf(Market(h2_price_usd_per_kg=1.0).export_max_mw)


def test_active_services_follow_components():
    p = plant(electrolyzer=None)
    names = [s.name for s in p.active_services()]
    assert "Ramp" not in names and "Spin" in names
    assert plant(market=market(as_mode="off")).active_services() == ()


def test_always_on_electrolyzer_needs_turbine_steam():
    tb = turbine(steam_total_mwth=151.0)
    with pytest.raises(ParameterError):
        plant(turbine=tb, electrolyzer=electrolyzer(commitment=False)).validate()


@pytest.mark.parametrize("iso", sorted(ISO_PRODUCTS))
def test_iso_service_sets_validate(iso):
    services = iso_services(iso)
    market(services=services).validate()
    for s in services:
        if s.balance_with:
            other = next(o for o in services if o.name == s.balance_with)
            assert other.column == s.column and s.price_share + other.price_share == pytest.approx(1.0)


def test_unknown_iso():
    with pytest.raises(ParameterError):
        iso_services("ABC")
