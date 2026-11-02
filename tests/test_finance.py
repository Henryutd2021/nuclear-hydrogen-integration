import math

import numpy as np
import pytest

from nuclear_h2 import (ParameterError, adjusted_basis, annualized_capex, credit_45u, crf,
                        exponent_from_learning_rate, irr, macrs_schedule, npv, payback_years, present_value,
                        scaled_cost)


def test_crf():
    r, n = 0.08, 20
    assert crf(r, n) == pytest.approx(r * (1 + r) ** n / ((1 + r) ** n - 1))
    assert crf(0.0, 25) == pytest.approx(1 / 25)
    # an annuity of crf * P over n years has present value P
    assert sum(crf(r, n) / (1 + r) ** k for k in range(1, n + 1)) == pytest.approx(1.0)
    assert annualized_capex(1e6, r, n) == pytest.approx(1e6 * crf(r, n))
    with pytest.raises(ParameterError):
        crf(0.05, 0)


def test_scaled_cost_and_learning_rate():
    assert scaled_cost(100.0, 20.0, 40.0, 0.8) == pytest.approx(100.0 * 2 ** 0.8)
    assert scaled_cost(100.0, 20.0, 0.0, 0.8) == 0.0
    a = exponent_from_learning_rate(0.12)
    # the cost per unit of size falls by 12 % per doubling of size
    unit = lambda s: scaled_cost(1.0, 1.0, s, a) / s  # noqa: E731
    assert unit(8.0) / unit(4.0) == pytest.approx(0.88)
    assert exponent_from_learning_rate(0.0) == 1.0
    with pytest.raises(ParameterError):
        exponent_from_learning_rate(1.0)
    with pytest.raises(ParameterError):
        scaled_cost(1.0, 0.0, 1.0, 1.0)


def test_macrs_five_year_matches_the_published_table():
    # IRS Publication 946, Table A-1 (half-year convention), 5-year property
    assert macrs_schedule(5) == pytest.approx([0.20, 0.32, 0.192, 0.1152, 0.1152, 0.0576])


@pytest.mark.parametrize("n", [3, 5, 10, 20])
def test_macrs_properties(n):
    s = macrs_schedule(n)
    assert len(s) == n + 1
    assert s.sum() == pytest.approx(1.0)
    assert np.all(s >= 0)
    factor = 2.0 if n <= 10 else 1.5
    assert s[0] == pytest.approx(factor / n / 2)
    assert s[-1] == pytest.approx(s[-2] / 2, rel=0.02)          # last year is a half year of straight line


def test_macrs_custom_factor():
    s = macrs_schedule(4, declining_balance=1.0)                 # straight line with half years
    assert s == pytest.approx([0.125, 0.25, 0.25, 0.25, 0.125])


def test_credit_45u_schedule():
    rate = credit_45u([10.0, 22.0, 31.0, 40.0, 55.0], max_credit=12.0, price_low=22.0, price_high=40.0)
    assert rate == pytest.approx([12.0, 12.0, 6.0, 0.0, 0.0])
    with pytest.raises(ParameterError):
        credit_45u([1.0], 12.0, 30.0, 30.0)


def test_adjusted_basis():
    assert adjusted_basis(1000.0, 400.0, 0.45) == pytest.approx(820.0)


def test_npv_irr_payback():
    cf = [-1000.0, 300.0, 400.0, 500.0, 200.0]
    r = 0.07
    assert npv(cf, r) == pytest.approx(sum(c / (1 + r) ** i for i, c in enumerate(cf)))
    assert present_value(cf, r) == npv(cf, r)
    x = irr(cf)
    assert npv(cf, x) == pytest.approx(0.0, abs=1e-6)
    # cumulative: -1000, -700, -300, +200 -> payback in the third operating year at 300/500
    assert payback_years(cf, first_operating_index=1) == pytest.approx(2.6)
    assert math.isnan(irr([100.0, 50.0]))
    assert math.isnan(payback_years([-10.0, 1.0, 1.0], 1))
    assert payback_years([5.0, 1.0], 0) == 0.0


def test_irr_prefers_the_root_nearest_zero_from_above():
    # conventional profile: one root
    assert irr([-100.0, 60.0, 60.0]) == pytest.approx(0.1306623, abs=1e-6)
    # non-conventional profile with a late outlay: two roots; the non-negative one is returned
    cf = [-100.0, 230.0, -132.0]
    x = irr(cf)
    assert x >= 0 and npv(cf, x) == pytest.approx(0.0, abs=1e-8)
    # only a negative root
    y = irr([-100.0, 40.0, 40.0])
    assert y < 0 and npv([-100.0, 40.0, 40.0], y) == pytest.approx(0.0, abs=1e-8)


def test_irr_respects_bounds_and_endpoints():
    assert math.isnan(irr([-100.0, 230.0, -132.0], high=0.05))       # roots at 0.1 and 0.2 lie outside
    assert irr([-100.0, 110.0], high=0.1) == pytest.approx(0.1)        # root at the upper bound
    assert irr([-100.0, 110.0], low=0.1, high=0.5) == pytest.approx(0.1)
    with pytest.raises(ParameterError):
        irr([-1.0, 2.0], low=0.2, high=0.1)


def test_irr_ignores_numerically_tiny_npv_far_from_a_root():
    cf = [0.0] * 14 + [-1e9, 1e9]                                       # root at 0 only
    assert math.isnan(irr(cf, low=0.1, high=10.0))
    assert irr(cf) == pytest.approx(0.0, abs=1e-9)


def test_irr_without_a_root_despite_overflow():
    assert math.isnan(irr([1.0] * 160))                                  # all positive: no root


@pytest.mark.parametrize("cf, expected", [
    ([0.0] * 400 + [1.0], None),               # no sign change
    ([-1.0, 0.02] + [0.0] * 200, -0.98),        # trailing zeros do not change the root
    ([-1e308, 1e308], 0.0),                     # scaled before discounting
])
def test_irr_numerical_edges(cf, expected):
    x = irr(cf)
    if expected is None:
        assert math.isnan(x)
    else:
        assert x == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize("cf, root", [
    ([-1e-200] + [0.0] * 999 + [1e200], 1.5118864315095801),
    ([-1e200] + [0.0] * 999 + [1e-200], -0.6018928294465028),
])
def test_irr_extreme_scales(cf, root):
    assert irr(cf) == pytest.approx(root, rel=1e-8)
