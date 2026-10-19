"""The audit log: every lawyer action, and the admin's actions on accounts (SPEC.md §7).

It is append-only: entries are only ever added, meant to be through `record`. Every way
the models offer to change or delete an entry refuses, including bulk QuerySet methods,
the manager Django uses internally and the update that save_base() and fixtures would
run. What bypasses the models is outside this guard: raw SQL and migrations. Entries hold keys and identifiers, never text typed by
anyone (CLAUDE.md).

Each entry names the applicant or lawyer concerned and, on the admin's actions, the admin.
A lawyer's own actions also name their case and, where there is one, the clip: as a plain
id, so that deleting a clip never deletes or changes an entry. A search records how many
clips it found instead (SPEC.md §7). The admin's decision on a larger case names the case.
One entry names nobody: a change of the size caps or alert limits found when the archive
starts, with the numbers before and after (audit.limits). The admin's temporary raise of
one lawyer's alert limit records the limit before and after too.
"""

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from accounts.models import LawyerProfile


class Action(models.TextChoices):
    INVITATION = 'invitation', _('Invitation')
    DECLINING = 'declining', _('Declining')
    ADMISSION = 'admission', _('Admission')
    SUSPENSION = 'suspension', _('Suspension')
    REINSTATEMENT = 'reinstatement', _('Reinstatement')
    REVOCATION = 'revocation', _('Revocation')
    # Lawyers' actions. The context keeps these nouns apart from the same English words
    # on buttons ("Download": Téléchargement, not Télécharger).
    CASE_CREATION = 'case_creation', pgettext_lazy('audit log action', 'Case creation')
    CASE_CLOSING = 'case_closing', pgettext_lazy('audit log action', 'Case closing')
    LARGER_CASE_REQUEST = 'larger_case_request', pgettext_lazy('audit log action', 'Request for a larger case')
    SEARCH = 'search', pgettext_lazy('audit log action', 'Search')
    PREVIEW = 'preview', pgettext_lazy('audit log action', 'Preview')
    DOWNLOAD = 'download', pgettext_lazy('audit log action', 'Download')
    CLIP_VIEW = 'clip_view', pgettext_lazy('audit log action', "Viewing a clip's page")
    CASE_APPROVAL = 'case_approval', pgettext_lazy('audit log action', 'Approval of a larger case')
    CASE_REFUSAL = 'case_refusal', pgettext_lazy('audit log action', 'Refusal of a larger case')
    LIMITS_CHANGE = 'limits_change', pgettext_lazy('audit log action', 'Change of the size caps and alert limits')
    ALERT_LIMIT_RAISE = 'alert_limit_raise', pgettext_lazy('audit log action', "Temporary raise of a lawyer's alert limit")


# Actions the admin takes; every other action is a lawyer's own, or about the archive.
ADMIN_ACTIONS = [
    Action.INVITATION, Action.DECLINING, Action.ADMISSION, Action.SUSPENSION,
    Action.REINSTATEMENT, Action.REVOCATION, Action.CASE_APPROVAL, Action.CASE_REFUSAL, Action.ALERT_LIMIT_RAISE,
]
# The admin's decisions on a lawyer's case.
ADMIN_CASE_ACTIONS = [Action.CASE_APPROVAL, Action.CASE_REFUSAL]
# Lawyers' own actions on a case.
CASE_ACTIONS = [
    Action.CASE_CREATION, Action.CASE_CLOSING, Action.LARGER_CASE_REQUEST, Action.SEARCH,
    Action.PREVIEW, Action.DOWNLOAD, Action.CLIP_VIEW,
]
CLIP_ACTIONS = [Action.PREVIEW, Action.DOWNLOAD, Action.CLIP_VIEW]
COUNT_ACTIONS = [Action.SEARCH]
# Entries about the archive itself, which name no applicant or lawyer.
ARCHIVE_ACTIONS = [Action.LIMITS_CHANGE]
# Entries that record limits, before and after.
LIMIT_ACTIONS = [Action.LIMITS_CHANGE, Action.ALERT_LIMIT_RAISE]
# The settings a change of limits records (audit.limits): each new size cap or alert limit
# is added here.
LIMIT_SETTINGS = [
    # Size caps on cases, and the ceilings above them.
    'CASE_WINDOW_CEILING_MINUTES', 'CASE_AREA_CEILING_METRES', 'CASE_WINDOW_CAP_MINUTES', 'CASE_AREA_CAP_METRES',
    # Alert limits.
    'ALERT_FAILED_LOGINS', 'QUEUE_ALERT_CLIPS', 'QUEUE_ALERT_HOURS',
    'ALERT_DOWNLOADS', 'ALERT_DOWNLOADS_MINUTES', 'ALERT_CASES', 'ALERT_CASES_HOURS', 'ALERT_RAISE_MAX_DAYS',
]


class AppendOnly(Exception):
    """Something tried to change or delete an audit entry."""


class AuditEntryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnly('Audit entries cannot be changed.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise AppendOnly('Audit entries cannot be changed.')

    def bulk_create(self, objs, *args, **kwargs):
        # It skips save(), and with update_conflicts it overwrites existing entries.
        raise AppendOnly('Audit entries are added one at a time, through record().')

    def delete(self):
        raise AppendOnly('Audit entries cannot be deleted.')


class AuditEntry(models.Model):
    """One action in the audit log: what was done, about whom, by the admin or not, and when.

    A lawyer's actions also record the case, and the clip or the number of clips found.
    """

    action = models.CharField(max_length=30, choices=Action.choices)
    # The applicant or lawyer the entry is about: the lawyer who acted, or the person
    # whose account or case the admin decided on. Empty only on entries about the archive.
    lawyer_profile = models.ForeignKey(
        LawyerProfile, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    # The admin's account, on the admin's actions; empty on all others.
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
    )
    # The case the action was taken in. Cases are never deleted (cases.models).
    case = models.ForeignKey(
        'cases.Case', null=True, blank=True, on_delete=models.PROTECT, related_name='audit_entries',
    )
    # The clip previewed or downloaded: its id alone, never a link that deleting the clip
    # would follow.
    clip_id = models.UUIDField(null=True, blank=True)
    # A search's results: its core matches (in the case's area and time window) and its
    # margin matches (found only in the margin around them).
    core_count = models.PositiveIntegerField(null=True, blank=True)
    margin_count = models.PositiveIntegerField(null=True, blank=True)
    # A change of limits: {"before": {setting: number}, "after": {setting: number}}, with
    # "before" null the first time. The admin raising one lawyer's alert limit: the limit
    # before and after, and "days": for how long. Setting names and numbers only.
    limits = models.JSONField(null=True, blank=True)
    # The time of writing, never one passed in. Precise: alerts look for many actions in a
    # short time (SPEC.md §7).
    at = models.DateTimeField(auto_now_add=True)

    objects = AuditEntryQuerySet.as_manager()

    class Meta:
        # Django's internal updates and deletions go through this manager too.
        base_manager_name = 'objects'
        # Counting one lawyer's recent actions of one kind: the activity alerts, after each
        # download and case creation, and the checks for a repeat preview or view.
        indexes = [models.Index(fields=['lawyer_profile', 'action', 'at'], name='entries_by_lawyer_action_time')]
        constraints = [
            models.CheckConstraint(condition=Q(action__in=Action.values), name='known_action'),
            models.CheckConstraint(
                condition=Q(action__in=ADMIN_ACTIONS, admin__isnull=False)
                | (~Q(action__in=ADMIN_ACTIONS) & Q(admin__isnull=True)),
                name='admin_on_admin_actions_only',
            ),
            models.CheckConstraint(
                condition=Q(action__in=ARCHIVE_ACTIONS, lawyer_profile__isnull=True)
                | (~Q(action__in=ARCHIVE_ACTIONS) & Q(lawyer_profile__isnull=False)),
                name='nobody_on_archive_actions_only',
            ),
            models.CheckConstraint(
                condition=Q(action__in=CASE_ACTIONS + ADMIN_CASE_ACTIONS, case__isnull=False)
                | (~Q(action__in=CASE_ACTIONS + ADMIN_CASE_ACTIONS) & Q(case__isnull=True)),
                name='case_on_case_actions_only',
            ),
            models.CheckConstraint(
                condition=Q(action__in=CLIP_ACTIONS, clip_id__isnull=False)
                | (~Q(action__in=CLIP_ACTIONS) & Q(clip_id__isnull=True)),
                name='clip_on_clip_actions_only',
            ),
            models.CheckConstraint(
                condition=Q(action__in=COUNT_ACTIONS, core_count__isnull=False, margin_count__isnull=False)
                | (~Q(action__in=COUNT_ACTIONS) & Q(core_count__isnull=True, margin_count__isnull=True)),
                name='counts_on_searches_only',
            ),
            models.CheckConstraint(
                condition=Q(action__in=LIMIT_ACTIONS, limits__isnull=False)
                | (~Q(action__in=LIMIT_ACTIONS) & Q(limits__isnull=True)),
                name='limits_on_limit_actions_only',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnly('Audit entries cannot be changed.')
        if self.action not in Action.values:
            raise ValueError(f'{self.action!r} is not an audit log action.')
        # The stored account, not the copy passed in, which may have been changed unsaved.
        if self.admin_id is not None and not get_user_model().objects.filter(
            pk=self.admin_id, is_staff=True,
        ).exists():
            raise ValueError("Only the admin's account can be the admin on an audit entry.")
        # The stored case, as above: an entry is about the case's own lawyer.
        if self.case_id is not None and not apps.get_model('cases', 'Case').objects.filter(
            pk=self.case_id, lawyer_profile_id=self.lawyer_profile_id,
        ).exists():
            raise ValueError("An audit entry about a case names the case's lawyer.")
        if self.action == Action.LIMITS_CHANGE and not _only_numbers_of_settings(self.limits):
            raise ValueError('A change of limits records setting names and whole numbers, nothing else.')
        if self.action == Action.ALERT_LIMIT_RAISE and not _a_raise(self.limits):
            raise ValueError('A raise records one activity limit before and after, higher, and for how many days.')
        # Always a new row: an entry given an existing id fails instead of replacing it.
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def _do_update(self, *args, **kwargs):
        # Where every UPDATE of an instance ends, including save_base() and loading fixtures.
        raise AppendOnly('Audit entries cannot be changed.')

    def delete(self, *args, **kwargs):
        raise AppendOnly('Audit entries cannot be deleted.')


def _only_numbers_of_settings(limits):
    """Whether `limits` is {"before": numbers or None, "after": numbers}: never text from anyone."""
    def numbers(values):
        return isinstance(values, dict) and set(values) <= set(LIMIT_SETTINGS) and all(
            type(number) is int for number in values.values()
        )
    return (
        isinstance(limits, dict) and set(limits) == {'before', 'after'}
        and (limits['before'] is None or numbers(limits['before'])) and numbers(limits['after'])
    )


# The alert limits the admin can raise for one lawyer (alerts.models.AlertLimitRaise).
RAISABLE_LIMITS = {'ALERT_DOWNLOADS', 'ALERT_CASES'}


def _a_raise(limits):
    """Whether `limits` is {"before": {limit: n}, "after": {limit: higher n}, "days": d}."""
    if not (isinstance(limits, dict) and set(limits) == {'before', 'after', 'days'}):
        return False
    before, after, days = limits['before'], limits['after'], limits['days']
    return (
        isinstance(before, dict) and isinstance(after, dict) and len(after) == 1
        and set(before) == set(after) <= RAISABLE_LIMITS
        and all(type(n) is int for n in [*before.values(), *after.values(), days])
        and all(after[name] > before[name] for name in after) and days >= 1
    )


def record(
    action, lawyer_profile, admin=None, case=None, clip=None, core_count=None, margin_count=None, limits=None,
):
    """Add an entry to the audit log: the way every entry is meant to be written."""
    return AuditEntry.objects.create(
        action=action, lawyer_profile=lawyer_profile, admin=admin, case=case,
        clip_id=clip.pk if clip is not None else None, core_count=core_count, margin_count=margin_count,
        limits=limits,
    )
