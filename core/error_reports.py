"""Django's error reports, without any frame's variables (SPEC.md §13).

Django hides the variables a function declares sensitive only in that function's own frame:
the frames that call it, such as a manager's or a decorator's, still show them. So no frame
shows any here, and nothing typed by anyone (form data, places, codes, case labels) reaches
a report through a traceback's variables; reports still say where the error happened. What
else a report shows (the request's data, headers, exception messages) is in SPEC.md §13.
With DEBUG on, as in development, Django's debug page shows the variables, as its own filter
does: it is shown only to the developer, and never sent.
"""

from django.views.debug import SafeExceptionReporterFilter


class NoFrameVariablesFilter(SafeExceptionReporterFilter):
    def get_traceback_frame_variables(self, request, tb_frame):
        if not self.is_active(request):
            return super().get_traceback_frame_variables(request, tb_frame)
        return [(name, self.cleansed_substitute) for name in tb_frame.f_locals]
