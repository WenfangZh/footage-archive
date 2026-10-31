"""Requests for map data never reach a log (ADR 0006, SPEC.md §8).

They show roughly where a lawyer or an uploader is looking, and an uploader's carry their map
pass. Every one of Django's log handlers drops a record that names a path under one of the map
data's prefixes (settings.LOGGING): the development server's line for each request, and every
warning about a refused one, whichever logger writes it ("Not Found: …", "Forbidden (CSRF …): …").
The path is matched decoded, as Django routes it, so an encoded one is dropped too.
"""

import logging
import re
from urllib.parse import unquote

from .data import URL_PREFIXES

# A path starts the message, or follows a space, quote or colon.
MAP_DATA_PATH = re.compile('(?:^|[\\s"\':])(?:' + '|'.join(re.escape(prefix) for prefix in URL_PREFIXES) + ')')


class SkipMapData(logging.Filter):
    def filter(self, record):
        return not MAP_DATA_PATH.search(unquote(record.getMessage()))
