"""Error reports show no frame's variables (core.error_reports, SPEC.md §13)."""

import sys

from django.views.debug import ExceptionReporter


def a_caller(secret):
    # Django would hide `secret` only in a frame that declares it sensitive; this one does not.
    return a_failing_function(secret)


def a_failing_function(value):
    raise RuntimeError('it failed')


def test_no_frame_of_a_report_shows_a_variable(settings):
    # As on a server, where reports could be sent; with DEBUG on, Django's debug page shows them.
    settings.DEBUG = False
    # Built here, so that it is in no line of source a report quotes, only in variables.
    secret = '-'.join(['Secret', 'Value'])
    try:
        a_caller(secret)
    except RuntimeError:
        exc_info = sys.exc_info()
    reporter = ExceptionReporter(None, *exc_info)
    frames = reporter.get_traceback_data()['frames']
    assert {frame['function'] for frame in frames} >= {'a_caller', 'a_failing_function'}
    # Compared with the variable, never a literal: reports quote the source lines around each frame.
    assert secret not in reporter.get_traceback_text() and secret not in reporter.get_traceback_html()
