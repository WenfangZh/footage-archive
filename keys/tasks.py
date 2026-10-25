"""Rotations of the group key in the background worker (keys.rotation, docs/m5-encryption-design.md §5.5):
every hour, a rotation waiting for enough lawyers tries to start, and a role left undone too
long is given up or drawn again."""

from huey import crontab
from huey.contrib.djhuey import db_periodic_task

from . import rotation


@db_periodic_task(crontab(minute='23'))
def rotation_tick():
    rotation.tick()
