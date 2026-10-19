"""How the database is set up (config/settings.py)."""

import sqlite3
import threading

import pytest
from django.db import connection, connections, transaction

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_transaction_takes_the_write_lock_when_it_begins():
    """A second writer waits for the first instead of failing with "database is locked"."""
    with transaction.atomic():
        # SQLite reports the write lock as held only to a connection that has it.
        other = sqlite3.connect(connection.settings_dict['NAME'], timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute('BEGIN IMMEDIATE')
        finally:
            other.close()
