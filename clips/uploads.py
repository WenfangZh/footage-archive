"""Receiving files: the size cap, enforced while the file streams in (SPEC.md §8).

Django passes each chunk of an incoming file through its upload handlers, in the
order of settings.FILE_UPLOAD_HANDLERS. This one comes first: it counts the bytes and
stops the request as soon as the cap is passed, before the next handler writes the
chunk to quarantine, so an oversize file is never kept whole or in part.

Stopping resets the connection rather than reading the rest of a file that may be
many gigabytes, so the browser may show its own error instead of our page. That is
why the form also checks the size in the browser before sending.
"""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.utils.formats import number_format
from django.utils.translation import gettext

# Room for the form's other fields and multipart boundaries in the declared length.
FORM_OVERHEAD_BYTES = 1_000_000


class SizeCapUploadHandler(FileUploadHandler):
    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        # Stopping here would escape as a server error, so the check on the declared
        # length waits for the start of the file part.
        self.declared_too_large = content_length > settings.UPLOAD_MAX_BYTES + FORM_OVERHEAD_BYTES
        self.received = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        if self.declared_too_large:
            self.stop()

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > settings.UPLOAD_MAX_BYTES:
            self.stop()
        return raw_data

    def file_complete(self, file_size):
        return None

    def stop(self):
        self.request.upload_too_large = True
        raise StopUpload(connection_reset=True)


def size_limit_display():
    """The size cap for people to read, e.g. '2 GB', counted in decimal as phones do."""
    limit = settings.UPLOAD_MAX_BYTES
    if limit >= 1_000_000_000:
        return gettext('%(size)s GB') % {'size': one_decimal(limit / 1_000_000_000)}
    if limit >= 1_000_000:
        return gettext('%(size)s MB') % {'size': one_decimal(limit / 1_000_000)}
    return gettext('%(size)s bytes') % {'size': number_format(limit)}


def one_decimal(number):
    """'2' rather than '2.0', '1.5' (or '1,5' in French) otherwise."""
    rounded = round(number, 1)
    return number_format(int(rounded) if rounded == int(rounded) else rounded)
