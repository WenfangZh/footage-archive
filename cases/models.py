"""Cases (SPEC.md §7): a lawyer's search scope for one incident.

A case has a case label, a time window and an area, and a margin around both; the window,
area and margin are fixed once the case is created, which goes through
`Case.objects.create_with_area`. A case is opened only for a lawyer, and never deleted: a
closed case stays as it was, and in the audit log. Closing goes through `close`.

A case over the size caps is made only with the lawyer's reason, and waits for the admin's
decision in its CaseApproval: until it is approved, it cannot be used; refused, it is
closed. A case within the caps has no CaseApproval.
"""

from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.models import LawyerProfile
from audit.models import Action, record
from cases import size
from clips import grid


class CaseQuerySet(models.QuerySet):
    def open(self):
        return self.filter(closed_at__isnull=True)

    def usable(self):
        """Open cases that can be searched: within the caps, or approved by the admin."""
        return self.open().filter(Q(approval__isnull=True) | Q(approval__approved=True))

    def close(self, because, now=None):
        """Close the open cases among these; closed ones keep their time and reason.

        A request for a larger case that is still waiting goes with it, and so does its reason.
        """
        with transaction.atomic():
            closing = list(self.open().values_list('pk', flat=True))
            CaseApproval.objects.filter(case__in=closing, decided_at__isnull=True)._delete_reasons()
            return super(CaseQuerySet, self.filter(pk__in=closing)).update(
                closed_at=now or timezone.now(), closed_because=because,
            )

    def create_with_area(
        self, lawyer_profile, label, starts_at, ends_at, cells, margin_minutes, margin_rings, approval_reason='',
    ):
        """Open a case whose area is a set of the grid's finest cells: the only way to make one.

        Raises size.CaseTooLarge for a case beyond the ceiling, or over the size caps
        without an `approval_reason`; with one, the case waits for the admin's approval.
        A reason given for a case within the caps is not kept.
        """
        cells = set(cells)
        if not cells or not all(grid.is_cell(cell) and grid.resolution(cell) == grid.finest_resolution() for cell in cells):
            raise ValueError("A case's area is a set of the grid's finest cells.")
        if not (timezone.is_aware(starts_at) and timezone.is_aware(ends_at) and starts_at < ends_at):
            raise ValueError("A case's time window has time zones, and ends after it starts.")
        if starts_at.year < 1900 or ends_at.year > 9998:
            # Near the ends of what Python's dates hold, converting to UTC overflows.
            raise ValueError("A case's time window is in the years a database can store.")
        for margin in (margin_minutes, margin_rings):
            # A bool is an int in Python; an absurd number of rings is caught by the size check.
            if type(margin) is not int or margin < 0:
                raise ValueError("A case's margin is a whole number, zero or more.")
        try:
            size.check(starts_at, ends_at, cells, margin_minutes, margin_rings)
            needs_approval = False
        except size.CaseTooLarge as too_large:
            if too_large.beyond_ceiling or not approval_reason.strip():
                raise
            needs_approval = True
        with transaction.atomic():
            case = self.model(
                lawyer_profile=lawyer_profile, label=label, starts_at=starts_at, ends_at=ends_at,
                margin_minutes=margin_minutes, margin_rings=margin_rings,
            )
            case.save(_checked=True)
            CaseCell.objects.bulk_create((CaseCell(case=case, cell=cell) for cell in cells), _checked=True)
            if needs_approval:
                CaseApproval(case=case, reason=approval_reason.strip()).save(_checked=True)
        return case

    def create(self, **kwargs):
        raise ValueError('Cases are made only through create_with_area(), which checks their size.')

    def bulk_create(self, objs, *args, **kwargs):
        raise ValueError('Cases are made only through create_with_area(), which checks their size.')

    def update(self, **kwargs):
        raise ValueError('Cases change only by being closed, through close().')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('Cases change only by being closed, through close().')

    def delete(self):
        raise ValueError('Cases are never deleted.')


class Case(models.Model):
    class ClosedBecause(models.TextChoices):
        LAWYER = 'lawyer', _('Closed by its lawyer')
        REVOCATION = 'revocation', _("Closed when its lawyer's access was revoked")
        INACTIVITY = 'inactivity', _('Closed after a long time without use')
        REFUSAL = 'refusal', _('Closed when the admin refused it')

    lawyer_profile = models.ForeignKey(LawyerProfile, on_delete=models.PROTECT, related_name='cases')
    # Never a client's name (SPEC.md §7).
    label = models.CharField(max_length=100)
    opened_at = models.DateTimeField(default=timezone.now)
    # Taken from the police report (SPEC.md §7). The margin widens the window at both ends,
    # and the area by rings of the grid's finest cells.
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    margin_minutes = models.PositiveSmallIntegerField()
    margin_rings = models.PositiveSmallIntegerField()
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_because = models.CharField(max_length=20, choices=ClosedBecause.choices, blank=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        base_manager_name = 'objects'
        constraints = [
            models.CheckConstraint(
                condition=Q(closed_because__in=['', 'lawyer', 'revocation', 'inactivity', 'refusal']),
                name='known_reason',
            ),
            models.CheckConstraint(
                condition=Q(closed_at__isnull=True, closed_because='')
                | (Q(closed_at__isnull=False) & ~Q(closed_because='')),
                name='closed_with_a_reason',
            ),
            models.CheckConstraint(condition=Q(ends_at__gt=F('starts_at')), name='window_ends_after_it_starts'),
        ]

    FIXED_FIELDS = ('lawyer_profile_id', 'starts_at', 'ends_at', 'margin_minutes', 'margin_rings')

    def save(self, *args, _checked=False, **kwargs):
        if self._state.adding:
            if not _checked:
                raise ValueError('Cases are made only through create_with_area(), which checks their size.')
            if LawyerProfile.objects.filter(pk=self.lawyer_profile_id).values_list('status', flat=True).first() != (
                LawyerProfile.Status.LAWYER
            ):
                raise ValueError('Only a lawyer can have a new case.')
            # Always a new row: a case given an existing id fails instead of replacing it.
            kwargs['force_insert'] = True
        else:
            stored = Case.objects.filter(pk=self.pk).values('closed_at', *self.FIXED_FIELDS).get()
            if stored['closed_at'] is not None:
                raise ValueError('A closed case never changes again.')
            if any(stored[field] != getattr(self, field) for field in self.FIXED_FIELDS):
                raise ValueError("A case's lawyer, time window and margin never change.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Cases are never deleted.')

    def area(self):
        return set(self.cells.values_list('cell', flat=True))

    def waiting_for_approval(self):
        return CaseApproval.objects.filter(case=self, decided_at__isnull=True).exists()


REASON_MAX_LENGTH = 2000
ADMIN_NOTE_MAX_LENGTH = 1000


class CaseApprovalQuerySet(models.QuerySet):
    def waiting(self):
        return self.filter(decided_at__isnull=True, case__closed_at__isnull=True)

    def _delete_reasons(self):
        return super().update(reason='')

    def update(self, **kwargs):
        raise ValueError('A request for a larger case changes only through decide().')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('A request for a larger case changes only through decide().')

    def bulk_create(self, objs, *args, **kwargs):
        raise ValueError('A request for a larger case is made only with its case, by create_with_area().')

    def delete(self):
        raise ValueError('Requests for larger cases are never deleted.')


class CaseApproval(models.Model):
    """A case over the size caps, its lawyer's reason, and the admin's decision (SPEC.md §7).

    The reason is for the admin to decide with: it is deleted once they have, or once the
    case is closed. The admin's own note, if any, is kept.
    """

    case = models.OneToOneField(Case, on_delete=models.PROTECT, primary_key=True, related_name='approval')
    reason = models.TextField(max_length=REASON_MAX_LENGTH, blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    approved = models.BooleanField(null=True)
    admin_note = models.TextField(max_length=ADMIN_NOTE_MAX_LENGTH, blank=True)

    objects = CaseApprovalQuerySet.as_manager()

    class Meta:
        base_manager_name = 'objects'
        constraints = [
            models.CheckConstraint(
                condition=Q(decided_at__isnull=True, approved__isnull=True, admin_note='')
                | Q(decided_at__isnull=False, approved__isnull=False, reason=''),
                name='decided_once_with_the_reason_deleted',
            ),
        ]

    def save(self, *args, _checked=False, **kwargs):
        if not (self._state.adding and _checked):
            raise ValueError('A request for a larger case is made only with its case, by create_with_area().')
        kwargs['force_insert'] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Requests for larger cases are never deleted.')

    def decide(self, approved, admin, note=''):
        """Approve or refuse the case, once: delete the reason, close a refused case, and log it."""
        with transaction.atomic():
            updated = super(CaseApprovalQuerySet, CaseApproval.objects.waiting().filter(pk=self.pk)).update(
                decided_at=timezone.now(), approved=approved, admin_note=note.strip(), reason='',
            )
            if updated != 1:
                raise ValueError('This request has already been decided, or its case closed.')
            case = Case.objects.get(pk=self.pk)
            if not approved:
                Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.REFUSAL)
            action = Action.CASE_APPROVAL if approved else Action.CASE_REFUSAL
            record(action, case.lawyer_profile, admin=admin, case=case)
        self.refresh_from_db()


class CaseCellQuerySet(models.QuerySet):
    def bulk_create(self, objs, *args, _checked=False, **kwargs):
        objs = list(objs)
        cases = {obj.case_id for obj in objs}
        if not _checked or len(cases) != 1 or self.filter(case_id__in=cases).exists():
            raise ValueError("A case's area is added only with the case, by create_with_area().")
        return super().bulk_create(objs, *args, **kwargs)

    def update(self, **kwargs):
        raise ValueError("A case's area never changes.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("A case's area never changes.")

    def delete(self):
        raise ValueError("A case's area never changes.")


class CaseCell(models.Model):
    """One of the grid's finest cells in a case's area, added with the case and kept as is.

    An area is a set of cells rather than a centre and radius, so that a map can later let
    the lawyer pick cells one by one. The coordinates typed to make it are never kept.
    """

    case = models.ForeignKey(Case, on_delete=models.PROTECT, related_name='cells')
    cell = models.BigIntegerField()

    objects = CaseCellQuerySet.as_manager()

    class Meta:
        base_manager_name = 'objects'
        constraints = [models.UniqueConstraint(fields=['case', 'cell'], name='each_cell_once_per_case')]

    def save(self, *args, **kwargs):
        raise ValueError("A case's area is added only with the case, by create_with_area().")

    def delete(self, *args, **kwargs):
        raise ValueError("A case's area never changes.")
