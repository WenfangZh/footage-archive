from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.roles import admin_required

from .models import Alert


@admin_required
@require_POST
def mark_seen(request, alert_id):
    alert = get_object_or_404(Alert, pk=alert_id, seen_at__isnull=True)
    alert.seen_at = timezone.now()
    alert.save(update_fields=['seen_at'])
    return redirect('manage:home')
