"""Django's error reports, without any frame's variables (SPEC.md §13).

Django hides the variables a function declares sensitive only in that function's own frame:
the frames that call it, such as a manager's or a decorator's, still show them. So no frame
shows any here, and nothing typed by anyone (form data, places, codes, case labels) reaches
a report through a traceback. Reports still say where the error happened. With DEBUG on,
Django's own debug page in development shows everything, as always.
"""

from django.views.debug import SafeExceptionReporterFilter


class NoFrameVariablesFilter(SafeExceptionReporterFilter):
    def get_traceback_frame_variables(self, request, tb_frame):
        return [(name, self.cleansed_substitute) for name in tb_frame.f_locals]
