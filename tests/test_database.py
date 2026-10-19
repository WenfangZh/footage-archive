"""How the database is set up (config/settings.py)."""

import sqlite3

import pytest
from django.db import connection, transaction

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_transaction_takes_the_write_lock_when_it_begins():
    """A second writer waits for the first instead of failing with "database is locked".

    The test database is SQLite's shared in-memory one, so this shows only that the
    transaction holds the lock from its start, not the wait a database file gives.
    """
    with transaction.atomic():
        # Its name is a URI under the tests, as Django opens it.
        other = sqlite3.connect(connection.settings_dict['NAME'], timeout=0, uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                other.execute('BEGIN IMMEDIATE')
        finally:
            other.close()
