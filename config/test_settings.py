"""Settings for the test suite: the normal settings, with test-only keys and data folder.

The keys are set before the normal settings load, so tests run on a fresh clone
without a .env file, and never use the keys from a developer's .env. Data (the
quarantine and stored media) goes to a temporary folder, never the developer's var/.
"""

import atexit
import os
import shutil
import tempfile

os.environ['DJANGO_SECRET_KEY'] = 'test-only-not-secret'
os.environ['UPLOAD_CODE_KEY'] = 'test-only-not-secret-test-only-not-secret'
# Kept in memory (pytest-django does the same for each test).
os.environ['EMAIL_BACKEND'] = 'django.core.mail.backends.locmem.EmailBackend'
os.environ['ARCHIVE_URL'] = 'https://archive.example.org'

_data_dir = tempfile.mkdtemp(prefix='footage-archive-tests-')
atexit.register(shutil.rmtree, _data_dir, ignore_errors=True)
os.environ['DJANGO_DATA_DIR'] = _data_dir

from .settings import *  # noqa: E402,F403

# Tasks run at once, in memory, without a worker.
HUEY = {**HUEY, 'immediate': True}  # noqa: F405

# Argon2 is slow on purpose, and most tests log someone in: a fast hasher here takes about
# a third off the suite. The real settings keep Argon2 (tests/test_settings.py checks it).
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
