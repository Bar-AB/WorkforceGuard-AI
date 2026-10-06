from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.errors import InvalidInputError
from app.services.bounds import EventQuery

START = datetime(2026, 1, 1, tzinfo=UTC)


def test_event_query_accepts_exact_max_window() -> None:
    query = EventQuery(START, START + timedelta(days=31))

    assert query.end - query.start == timedelta(days=31)


def test_event_query_rejects_window_over_max() -> None:
    with pytest.raises(InvalidInputError, match="31 days"):
        EventQuery(START, START + timedelta(days=31, microseconds=1))


def test_event_query_rejects_naive_datetime() -> None:
    with pytest.raises(InvalidInputError, match="time zone"):
        EventQuery(START, datetime(2026, 1, 2))


@pytest.mark.parametrize("end", [START, START - timedelta(hours=1)])
def test_event_query_rejects_end_not_after_start(end: datetime) -> None:
    with pytest.raises(InvalidInputError, match="after"):
        EventQuery(START, end)


@pytest.mark.parametrize("limit", [0, 501])
def test_event_query_rejects_limit_out_of_range(limit: int) -> None:
    with pytest.raises(InvalidInputError, match="limit"):
        EventQuery(START, START + timedelta(days=1), limit=limit)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (
            datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),
            datetime(1, 1, 2, tzinfo=timezone(timedelta(hours=5))),
        ),
        (
            datetime(9999, 12, 30, tzinfo=timezone(timedelta(hours=-5))),
            datetime(9999, 12, 31, 23, tzinfo=timezone(timedelta(hours=-5))),
        ),
        (datetime(1969, 12, 31, tzinfo=UTC), datetime(1970, 1, 2, tzinfo=UTC)),
        (datetime(2100, 12, 31, tzinfo=UTC), datetime(2101, 1, 1, 1, tzinfo=UTC)),
    ],
)
def test_event_query_rejects_window_outside_supported_years(start: datetime, end: datetime) -> None:
    with pytest.raises(InvalidInputError, match="between 1970 and 2100"):
        EventQuery(start, end)


def test_event_query_accepts_supported_year_edges() -> None:
    EventQuery(datetime(1970, 1, 1, tzinfo=UTC), datetime(1970, 1, 2, tzinfo=UTC))
    EventQuery(datetime(2100, 12, 30, tzinfo=UTC), datetime(2100, 12, 31, 23, tzinfo=UTC))
