from datetime import date

import pytest

from app.errors import InvalidInputError
from app.services.payroll import check_period_window


def test_payroll_period_window_accepts_exact_max_span() -> None:
    check_period_window(date(2026, 1, 1), date(2026, 2, 1))


def test_payroll_period_window_rejects_reversed_dates() -> None:
    with pytest.raises(InvalidInputError, match="period_to"):
        check_period_window(date(2026, 1, 10), date(2026, 1, 9))


def test_payroll_period_window_rejects_span_over_max() -> None:
    with pytest.raises(InvalidInputError, match="31 days"):
        check_period_window(date(2026, 1, 1), date(2026, 2, 2))
