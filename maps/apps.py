import logging

from django.apps import AppConfig
from django.urls import reverse


class MapsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'maps'

    def ready(self):
        # Requests for map data show roughly where a lawyer is looking: never logged (ADR 0006),
        # not even by Django's own request logs.
        for name in ('django.server', 'django.request'):
            logging.getLogger(name).addFilter(skip_map_data)


def skip_map_data(record):
    """False for a log record about a request for map data, which drops it."""
    prefix = reverse('lawyer:map_style').rsplit('/', 1)[0] + '/'
    return prefix not in record.getMessage()
