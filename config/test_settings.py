"""Settings for the test suite: the normal settings, with a test-only secret key.

The key is set before the normal settings load, so tests run on a fresh clone
without a .env file, and never use the key from a developer's .env.
"""

import os

os.environ['DJANGO_SECRET_KEY'] = 'test-only-not-secret'

from .settings import *  # noqa: E402,F403
