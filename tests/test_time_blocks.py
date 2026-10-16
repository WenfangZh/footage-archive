import datetime

import pytest

from clips.time_blocks import four_hour_block

UTC = datetime.UTC


def utc(*args):
    return datetime.datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize('moment, block', [
    # Winter, Paris is UTC+1: 14:37 Paris is in the 12h block, 11:00 UTC.
    (utc(2026, 1, 15, 13, 37, 12), utc(2026, 1, 15, 11, 0)),
    # Summer, Paris is UTC+2: 23:59 Paris is in the 20h block, 18:00 UTC.
    (utc(2026, 6, 15, 21, 59, 59), utc(2026, 6, 15, 18, 0)),
    # A block start stays where it is.
    (utc(2026, 6, 15, 18, 0), utc(2026, 6, 15, 18, 0)),
    # 00:30 Paris belongs to the 00h block of its own day, not the day before in UTC.
    (utc(2026, 1, 14, 23, 30), utc(2026, 1, 14, 23, 0)),
])
def test_moments_fall_in_the_paris_block_that_contains_them(moment, block):
    assert four_hour_block(moment) == block


def test_spring_clock_change():
    # 29 March 2026: 02:00 Paris jumps to 03:00. 03:30 summer time is in the 00h block,
    # which started at midnight winter time.
    assert four_hour_block(utc(2026, 3, 29, 1, 30)) == utc(2026, 3, 28, 23, 0)
    assert four_hour_block(utc(2026, 3, 29, 2, 30)) == utc(2026, 3, 29, 2, 0)


def test_autumn_clock_change():
    # 25 October 2026: 03:00 Paris goes back to 02:00, so 02:30 happens twice.
    # Both are in the 00h block, which started at midnight summer time.
    assert four_hour_block(utc(2026, 10, 25, 0, 30)) == utc(2026, 10, 24, 22, 0)
    assert four_hour_block(utc(2026, 10, 25, 1, 30)) == utc(2026, 10, 24, 22, 0)
    assert four_hour_block(utc(2026, 10, 25, 3, 30)) == utc(2026, 10, 25, 3, 0)


def test_result_is_utc():
    assert four_hour_block(utc(2026, 1, 15, 13, 37)).tzinfo == UTC


def test_naive_datetimes_are_refused():
    with pytest.raises(ValueError, match='time zone'):
        four_hour_block(datetime.datetime(2026, 1, 15, 13, 37))
