import numpy as np
import pandas as pd
import pytest

from nuclear_h2 import (ParameterError, Service, TimeSeries, drop_leap_day, fill_gaps, hourly_year, long_to_wide,
                        scale_prices, to_hourly)
from nuclear_h2.synthetic import (AsPriceSettings, as_capacity_prices, mileage_factors, performance_factors,
                                  regulation_deployment, reserve_deployment, synthetic_series)

from conftest import SERVICES, series


def test_to_hourly_mean_of_sub_hourly_values():
    idx = pd.date_range("2031-03-02 00:00", periods=6, freq="20min")
    s = pd.Series([1.0, 3.0, 8.0, 10.0, 20.0, 45.0], index=idx)
    h = to_hourly(s)
    assert list(h.values) == [4.0, 25.0]
    assert h.index[1] == pd.Timestamp("2031-03-02 01:00")
    with pytest.raises(ParameterError):
        to_hourly(pd.Series([1.0, 2.0]))


def test_drop_leap_day_and_fill_gaps():
    idx = pd.date_range("2028-02-28", periods=72, freq="1h")
    s = pd.Series(np.arange(72, dtype=float), index=idx)
    assert len(drop_leap_day(s)) == 48
    g = pd.Series([np.nan, 2.0, np.nan, np.nan, 8.0, np.nan])
    assert list(fill_gaps(g)) == [2.0, 2.0, 4.0, 6.0, 8.0, 8.0]


@pytest.mark.parametrize("year", [2027, 2028])
def test_hourly_year_has_8760_values(year):
    idx = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="1h", inclusive="left")
    s = pd.Series(np.ones(len(idx)), index=idx).drop(idx[[5, 6, 100]])
    out = hourly_year(s, year)
    assert len(out) == 8760 and not out.isna().any()


def test_hourly_year_time_zone():
    idx = pd.date_range("2029-01-01", "2030-01-01", freq="1h", inclusive="left", tz="US/Central")
    s = pd.Series(np.arange(len(idx), dtype=float), index=idx.tz_convert("UTC"))
    assert len(hourly_year(s, 2029, tz="US/Central")) == 8760
    with pytest.raises(ParameterError):
        hourly_year(s, 2029)


def test_long_to_wide_with_mapping():
    rec = pd.DataFrame({"time": ["2031-01-01 00:00"] * 3 + ["2031-01-01 01:00"] * 3,
                        "product": ["REG", "SR", "XX"] * 2, "price": [4.0, 2.0, 9.0, 6.0, 3.0, 9.0]})
    wide = long_to_wide(rec, "time", "product", "price", rename={"REG": "Reg", "SR": "Spin"})
    assert list(wide.columns) == ["Reg", "Spin"]
    assert wide["Reg"].tolist() == [4.0, 6.0]


def test_validation_errors():
    ts = series([30.0, 40.0])
    ts.validate(SERVICES)
    bad = series([30.0, np.nan])
    with pytest.raises(ParameterError):
        bad.validate(SERVICES)
    with pytest.raises(ParameterError):
        series([30.0, 40.0], winning=1.2).validate(SERVICES)
    with pytest.raises(ParameterError):
        series([30.0, 40.0], as_price=-1.0).validate(SERVICES)
    with pytest.raises(ParameterError):
        series([30.0, 40.0]).validate([Service("Missing", "up")])
    with pytest.raises(ParameterError):
        TimeSeries(np.ones(3), pd.DataFrame({"a": [1.0, 2.0]}), pd.DataFrame(), pd.DataFrame())


def test_scale_prices_and_slice():
    ts = series([30.0, 40.0, 50.0])
    s = scale_prices(ts, 1.35)
    assert s.energy_price.tolist() == pytest.approx([40.5, 54.0, 67.5])
    assert s.as_price.equals(ts.as_price)
    assert scale_prices(ts, 1.35, include_as=True).as_price["Spin"].iloc[0] == pytest.approx(9.45)
    part = ts.slice(1, 3)
    assert part.hours == 2 and part.energy_price.tolist() == [40.0, 50.0]
    assert ts.annualization == pytest.approx(8760 / 3)


def test_as_price_generator_form():
    rng = np.random.default_rng(3)
    lmp = np.full(20000, 40.0)
    st = AsPriceSettings(ratio_low=0.1, ratio_high=0.2, noise_sd=0.0, spike_prob=0.1, spike_low=20.0, spike_high=30.0)
    p = as_capacity_prices(lmp, rng, st)
    assert p.min() >= 0
    base = (p >= 4.0 - 1e-9) & (p <= 8.0 + 1e-9)
    spiked = p > 8.0 + 1e-9
    assert base.mean() + spiked.mean() == pytest.approx(1.0)
    assert spiked.mean() == pytest.approx(0.1, abs=0.01)
    assert as_capacity_prices(np.full(5, -50.0), rng, st).min() == 0.0


def test_deployment_and_regulation_generators():
    rng = np.random.default_rng(5)
    up, down = regulation_deployment(2000, rng, total_cap=0.7)
    assert np.all(up >= 0) and np.all(down >= 0) and np.all(up + down <= 0.7 + 1e-12)
    assert up.mean() > down.mean()
    r = reserve_deployment(5000, rng, probability=0.2, magnitude=(0.3, 0.4))
    nz = r[r > 0]
    assert nz.min() >= 0.3 and nz.max() <= 0.4 and (r > 0).mean() == pytest.approx(0.2, abs=0.03)
    m = mileage_factors(1000, rng, base=2.0, noise_fraction=0.5, clip=(0.8, 1.2))
    assert m.min() >= 1.6 - 1e-12 and m.max() <= 2.4 + 1e-12
    pf = performance_factors(1000, rng, mean=0.85, sd=0.2, low=0.65, high=0.97)
    assert pf.min() >= 0.65 and pf.max() <= 0.97


def test_synthetic_series_is_deterministic():
    a, b = synthetic_series(48, seed=4), synthetic_series(48, seed=4)
    assert np.array_equal(a.energy_price, b.energy_price) and a.as_price.equals(b.as_price)
    assert not np.array_equal(a.energy_price, synthetic_series(48, seed=5).energy_price)


def test_separate_regulation_products_are_generated_as_pairs():
    from nuclear_h2 import iso_services

    services = iso_services("ERCOT", mileage=True)
    ts = synthetic_series(500, services=services)
    up, down = ts.deployment["RegU"].to_numpy(), ts.deployment["RegD"].to_numpy()
    assert (up > 0).mean() > 0.9 and (down > 0).mean() > 0.9          # regulation generator, not Bernoulli reserves
    assert np.allclose(ts.mileage["RegU"], ts.mileage["RegD"])
    assert np.allclose(ts.performance["RegU"], ts.performance["RegD"])
    assert (ts.deployment["NSpin"] == 0).mean() > 0.5                   # reserves stay event-based


@pytest.mark.parametrize("hours", [1, 2, 5])
def test_short_synthetic_horizons(hours):
    ts = synthetic_series(hours)
    assert ts.hours == hours and len(ts.deployment) == hours
    assert synthetic_series(hours, services=()).as_price.shape == (hours, 0)


def test_mileage_price_must_be_finite():
    ts = series([30.0, 40.0])
    ts.mileage_price.iloc[0, 0] = np.nan
    with pytest.raises(ParameterError):
        ts.validate(SERVICES)


def test_linked_products_share_one_profile():
    services = (Service("Aup", "up", product="A", mileage=True, pair_with="Bdn"),
                Service("Bdn", "down", product="B", mileage=True),
                Service("Cdn", "down", product="C", mileage=True, pair_with="Dup"),
                Service("Dup", "up", product="A", mileage=True))
    ts = synthetic_series(50, services=services)
    for table in (ts.mileage, ts.performance, ts.mileage_price):
        assert np.allclose(table["A"], table["B"]) and np.allclose(table["A"], table["C"])


def test_product_groups_follow_pairs_not_billing_flags():
    services = (Service("Aup", "up", product="A", mileage=True, pair_with="Bdn"),
                Service("Bdn", "down", product="B", mileage=False),
                Service("Bup", "up", product="B", mileage=True, pair_with="Cdn"),
                Service("Cdn", "down", product="C", mileage=True))
    ts = synthetic_series(40, services=services)
    for table in (ts.mileage, ts.performance, ts.mileage_price):
        assert np.allclose(table["A"], table["B"]) and np.allclose(table["B"], table["C"])
