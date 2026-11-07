"""The 2024 PJM prices in data/pjm/: checksums, format, flags of values that are not PJM records, interpolation,
time series assembly and one short optimization on them."""

import re
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nuclear_h2 import (ParameterError, balance_residuals, iso_services, optimize, read_inputs, read_pjm_2024,
                        write_inputs)
from nuclear_h2.pjm import AS_FILE, ENERGY_FILE, SHA256, default_directory, file_sha256

from conftest import market, plant

DATA = Path(__file__).resolve().parents[1] / "data" / "pjm"


@pytest.fixture(scope="module")
def prices():
    return read_pjm_2024(DATA)


def test_checksums_agree_with_sums_file_readme_and_files():
    sums = dict(reversed(line.split()) for line in (DATA / "SHA256SUMS").read_text().splitlines() if line.strip())
    assert sums == SHA256
    readme = (DATA / "README.md").read_text()
    for name, digest in SHA256.items():
        assert file_sha256(DATA / name) == digest
        assert re.search(rf"{re.escape(name)}.*{digest}", readme), f"{name} checksum missing in README"


def test_default_directory_from_repository_root(monkeypatch):
    monkeypatch.chdir(DATA.parents[1])
    assert default_directory() == Path.cwd() / "data" / "pjm"


def test_hour_labels(prices):
    idx = prices.index
    assert len(idx) == 8760 and idx.is_unique and idx.is_monotonic_increasing
    assert idx[0] == pd.Timestamp("2024-01-01 00:00") and idx[-1] == pd.Timestamp("2024-12-31 23:00")
    assert not ((idx.month == 2) & (idx.day == 29)).any()
    assert set(pd.Series(idx.date).value_counts()) == {24}            # 365 days of 24 hour labels
    assert idx.tz is None
    assert prices.as_price.index.equals(idx) and prices.not_observed.index.equals(idx)


def test_columns_and_first_hour(prices):
    assert list(prices.as_price.columns) == ["Reg", "Rse", "Syn", "TMR"]
    assert set(prices.as_price.columns) == {s.column for s in iso_services("PJM")}
    first = prices.index[0]
    assert prices.energy_price[first] == pytest.approx(32.465)
    assert prices.as_price.loc[first].tolist() == pytest.approx([10.72, 0.04, 0.04, 0.0])
    assert (prices.as_price.to_numpy() >= 0).all()


def test_regulation_up_and_down_columns_repeat_one_price(prices):
    raw = pd.read_csv(DATA / AS_FILE, index_col="Timestamp", parse_dates=True)
    differ = raw.index[raw["p_RegUp_PJM"] != raw["p_RegDown_PJM"]]
    assert list(differ) == [pd.Timestamp("2024-03-10 02:00")]               # a flagged row
    assert prices.not_observed.loc[differ, "Reg"].all()
    assert (raw.filter(like="loc_").to_numpy() == 0).all()


def test_values_that_are_not_pjm_records(prices):
    flags = prices.not_observed
    assert flags["energy_price"].sum() == 1 and flags.at[pd.Timestamp("2024-03-10 02:00"), "energy_price"]
    for col in prices.as_price.columns:
        hours = flags.index[flags[col]]
        assert len(hours) == 25
        assert set(hours.strftime("%Y-%m-%d")) == {"2024-03-10", "2024-12-12"}
        dec12 = prices.as_price.loc["2024-12-12", col]
        assert len(dec12) == 24 and dec12.nunique() == 1                     # one constant fill value
    assert not flags.loc["2024-11-03"].to_numpy().any()


def test_interpolated_replaces_only_flagged_values(prices):
    filled = prices.interpolated()
    keep = ~prices.not_observed
    assert np.array_equal(filled.energy_price[keep["energy_price"]], prices.energy_price[keep["energy_price"]])
    for col in prices.as_price.columns:
        assert np.array_equal(filled.as_price.loc[keep[col], col], prices.as_price.loc[keep[col], col])
    T = pd.Timestamp
    e = filled.energy_price
    assert e[T("2024-03-10 02:00")] == pytest.approx((e[T("2024-03-10 01:00")] + e[T("2024-03-10 03:00")]) / 2)
    reg = filled.as_price["Reg"]
    a, b = reg[T("2024-12-11 23:00")], reg[T("2024-12-13 00:00")]
    assert np.allclose(reg.loc["2024-12-12"].to_numpy(), a + (b - a) * np.arange(1, 25) / 25)
    assert prices.energy_price[T("2024-03-10 02:00")] != pytest.approx(e[T("2024-03-10 02:00")])   # original kept


def test_modified_copy_is_refused_unless_verify_is_off(tmp_path):
    for name in (ENERGY_FILE, AS_FILE):
        shutil.copy(DATA / name, tmp_path / name)
    text = (tmp_path / ENERGY_FILE).read_text().replace("32.465", "32.466", 1)
    (tmp_path / ENERGY_FILE).write_text(text)
    with pytest.raises(ParameterError, match="SHA-256"):
        read_pjm_2024(tmp_path)
    assert read_pjm_2024(tmp_path, verify=False).energy_price.iloc[0] == pytest.approx(32.466)


@pytest.mark.parametrize("edit", ["drop_row", "rename", "nan", "negative", "year", "minute", "foreign_hour",
                                  "regdown"])
def test_format_errors(tmp_path, edit):
    for name in (ENERGY_FILE, AS_FILE):
        shutil.copy(DATA / name, tmp_path / name)
    target = tmp_path / (ENERGY_FILE if edit in ("drop_row", "nan", "year", "minute", "foreign_hour") else AS_FILE)
    df = pd.read_csv(target)
    if edit == "drop_row":
        df = df.iloc[:-1]
    elif edit == "rename":
        df = df.rename(columns={"p_Syn_PJM": "p_Spin_PJM"})
    elif edit == "nan":
        df.iloc[5, 1] = np.nan
    elif edit == "negative":
        df.loc[7, "p_Rse_PJM"] = -1.0
    elif edit == "year":                                                    # every label moved to 2023
        df["Timestamp"] = df["Timestamp"].str.replace("2024-", "2023-", n=1, regex=False)
    elif edit == "minute":                                                  # half-hour labels
        df["Timestamp"] = df["Timestamp"].str.replace(":00:00", ":30:00", regex=False)
    elif edit == "foreign_hour":                                            # one 2024 hour replaced by a 2023 hour
        df = pd.concat([df.iloc[[0]].assign(Timestamp="2023-12-31 23:00:00"), df.iloc[1:]])
    else:                                                                   # regulation down differs in an ordinary hour
        df.loc[100, "p_RegDown_PJM"] = df.loc[100, "p_RegUp_PJM"] + 1.0
    df.to_csv(target, index=False)
    with pytest.raises(ParameterError):
        read_pjm_2024(tmp_path, verify=False)


def test_timeseries_selection_and_settings(prices):
    services = iso_services("PJM")
    ts = prices.timeseries(0.6, 0.1, services, start="2024-07-15 00:00", stop="2024-07-16 00:00")
    assert ts.hours == 24 and ts.index[0] == pd.Timestamp("2024-07-15 00:00")
    assert list(ts.as_price.columns) == ["Rse", "Reg", "Syn", "TMR"]
    assert list(ts.deployment.columns) == [s.name for s in services]
    assert np.array_equal(ts.energy_price, prices.energy_price.loc["2024-07-15"].to_numpy())
    by_rows = prices.timeseries(0.6, 0.1, services, start=4680, stop=4704)
    assert np.array_equal(by_rows.energy_price, ts.energy_price)
    whole = prices.timeseries(0.5, 0.0)
    assert whole.hours == 8760
    win = pd.DataFrame({p: np.full(24, 0.7) for p in ["Rse", "Reg", "Syn", "TMR"]})
    assert prices.timeseries(win, 0.1, services, start=0, stop=24).winning_rate.equals(win)


@pytest.mark.parametrize("kw", [dict(start=10, stop=10), dict(start=0, stop=9000), dict(start="2024-02-29 00:00"),
                                dict(winning=1.5), dict(winning=pd.DataFrame({"Reg": [0.5] * 24})),
                                dict(deployment=pd.DataFrame({s.name: [0.1] * 23 for s in iso_services("PJM")}))])
def test_timeseries_errors(prices, kw):
    args = dict(winning=0.6, deployment=0.1, start=0, stop=24)
    args.update(kw)
    with pytest.raises(ParameterError):
        prices.timeseries(args["winning"], args["deployment"], start=args["start"], stop=args["stop"])


def test_energy_only_timeseries(prices):
    ts = prices.timeseries(0.6, 0.1, services=(), stop=24)
    assert ts.hours == 24 and ts.as_price.shape == (24, 0) and ts.deployment.shape == (24, 0)
    op = optimize(plant(market=market(services=(), as_mode="off")), ts)
    assert max(balance_residuals(op).values()) < 1e-6


def test_timeseries_needs_pjm_products(prices):
    with pytest.raises(ParameterError, match="no PJM prices"):
        prices.timeseries(0.6, 0.1, services=iso_services("ERCOT"), start=0, stop=24)


def test_input_directory_and_optimization_on_pjm_prices(prices, tmp_path):
    services = iso_services("PJM")
    ts = prices.interpolated().timeseries(0.6, 0.15, services, start="2024-03-09 12:00", stop="2024-03-10 12:00")
    p = plant(market=market(services=services))
    write_inputs(tmp_path / "pjm", p, ts)
    p2, ts2 = read_inputs(tmp_path / "pjm")
    assert np.allclose(ts2.energy_price, ts.energy_price, rtol=0, atol=1e-9)        # CSV round trip
    assert list(ts2.index) == list(ts.index)
    op = optimize(p2, ts2)
    assert max(balance_residuals(op).values()) < 1e-6
    assert np.allclose(op.hourly["energy_price"].to_numpy(), ts.energy_price)


def test_dispatch_mode_on_pjm_prices(prices):
    services = iso_services("PJM")
    ts = prices.timeseries(0.6, 0.2, services, start="2024-07-15 00:00", stop="2024-07-15 12:00")
    p = plant(market=replace(market(services=services), as_mode="dispatch"))
    op = optimize(p, ts)
    assert max(balance_residuals(op).values()) < 1e-6
