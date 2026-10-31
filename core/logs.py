"""Upload addresses kept out of the archive's logs (SPEC.md §8).

A partial upload's address lets whoever holds it continue or abandon that upload, and the
address of the form that follows an upload lets them confirm it. Django's own log lines name
the path of a request: the development server's line for every request, and a warning for
each refused one ("Not Found: …", "Conflict: …"). Every handler therefore replaces the
random id in such a path before writing (settings.LOGGING).
"""

import logging
import re

# The paths of clips.urls that end in an upload's random id.
UPLOAD_ADDRESS = re.compile(r'(/upload/(?:resumable/)?)[0-9a-fA-F]{8}(?:-?[0-9a-fA-F]{4}){3}-?[0-9a-fA-F]{12}')


class HideUploadAddresses(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        hidden = UPLOAD_ADDRESS.sub(r'\1…', message)
        if hidden != message:
            record.msg, record.args = hidden, ()
        return True
