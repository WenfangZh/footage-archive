"""Upload and edit times are kept only to their 4-hour block (SPEC.md §6).

An exact time could be matched against connection logs to find the uploader. Blocks
start at 00h, 04h, 08h, 12h, 16h and 20h in the archive's time zone (Paris time);
clock changes happen at 02h or 03h, so a block start is never skipped or repeated.
"""

import datetime
from zoneinfo import ZoneInfo

from django.conf import settings

BLOCK_HOURS = 4


def four_hour_block(moment):
    """The start, in UTC, of the 4-hour block containing an aware datetime."""
    if moment.tzinfo is None:
        raise ValueError('four_hour_block needs a datetime with a time zone.')
    local = moment.astimezone(ZoneInfo(settings.TIME_ZONE))
    start = local.replace(
        hour=local.hour - local.hour % BLOCK_HOURS, minute=0, second=0, microsecond=0, fold=0,
    )
    return start.astimezone(datetime.UTC)
