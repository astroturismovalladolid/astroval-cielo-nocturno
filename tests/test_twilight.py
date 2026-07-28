import datetime as dt

import pytest

from twilight import clip_to_available_hours, date_range, night_hours, night_window

RELLO = dict(lat=41.367, lon=-2.761, elevation=1140)


@pytest.mark.parametrize("date", [dt.date(2025, 3, 21), dt.date(2025, 6, 21), dt.date(2025, 12, 21)])
def test_night_window_spans_midnight(date):
    start, end = night_window(**RELLO, date=date)
    assert start.date() == date
    assert end.date() == date + dt.timedelta(days=1)
    assert start < end
    assert start.tzinfo == dt.timezone.utc


def test_night_hours_are_hourly_and_within_window():
    date = dt.date(2025, 6, 21)
    start, end = night_window(**RELLO, date=date)
    hours = night_hours(**RELLO, date=date)

    assert hours == sorted(hours)
    for h in hours:
        assert start <= h <= end
        assert h.minute == 0 and h.second == 0
    # las horas consecutivas están separadas exactamente 1h
    diffs = {b - a for a, b in zip(hours, hours[1:])}
    assert diffs == {dt.timedelta(hours=1)}


def test_clip_to_available_hours():
    date = dt.date(2025, 12, 21)  # el amanecer astronómico cae tras las 05:00 UTC
    hours = night_hours(**RELLO, date=date)
    available = set(range(18, 24)) | set(range(0, 6))
    clipped = clip_to_available_hours(hours, available)

    assert len(clipped) <= len(hours)
    assert all(h.hour in available for h in clipped)


def test_date_range_inclusive():
    start = dt.date(2024, 1, 1)
    end = dt.date(2024, 1, 5)
    days = list(date_range(start, end))
    assert days[0] == start
    assert days[-1] == end
    assert len(days) == 5
