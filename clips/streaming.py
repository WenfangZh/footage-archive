"""Serving a file with HTTP byte ranges, so that browsers can play and seek a video.

Django's FileResponse ignores the Range header, and Safari will not play a video
without it. This works on any seekable binary file object, however the caller opened
it (the vault returns one; M5 makes it a decrypting one).
"""

import re

from django.http import HttpResponse, StreamingHttpResponse

CHUNK_SIZE = 64 * 1024
# One range only: a multi-range request does not match, and gets the whole file, which
# the HTTP spec allows.
RANGE = re.compile(r'bytes=(\d*)-(\d*)')


class _Slice:
    """Iterates over `length` bytes of the open file from its current position."""

    def __init__(self, file, length):
        self.file = file
        self.remaining = length

    def __iter__(self):
        return self

    def __next__(self):
        chunk = self.file.read(min(CHUNK_SIZE, self.remaining)) if self.remaining > 0 else b''
        if not chunk:
            raise StopIteration
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        # The response calls this when it is closed, even if it was never iterated.
        self.file.close()


def file_response(request, file, size, content_type):
    """Serve the open binary `file` of `size` bytes, or the byte range the request asks for.

    The response closes `file` when it is closed.
    """
    match = RANGE.fullmatch(request.headers.get('Range', ''))
    start, end = 0, size - 1
    status = 200
    if match and (match[1] or match[2]) and not (match[1] and match[2] and int(match[2]) < int(match[1])):
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last or size - 1), size - 1)
        else:  # A suffix range: the last `last` bytes.
            start = max(size - int(last), 0)
        if start >= size or start > end:
            file.close()
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{size}'
            return _with_headers(response)
        status = 206

    file.seek(start)
    length = end - start + 1
    response = StreamingHttpResponse(_Slice(file, length), status=status, content_type=content_type)
    response['Content-Length'] = length
    if status == 206:
        response['Content-Range'] = f'bytes {start}-{end}/{size}'
    return _with_headers(response)


def _with_headers(response):
    response['Accept-Ranges'] = 'bytes'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'no-store, private'
    return response
