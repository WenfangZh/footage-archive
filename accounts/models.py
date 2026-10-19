"""Accounts: who can log in, and in which role (SPEC.md §5).

An account is either the admin's or an applicant's or lawyer's, never both:
- the admin's account is staff and superuser, created with `python manage.py createsuperuser`;
- an applicant's or lawyer's account has a LawyerProfile, whose status says where they stand.

People log in with their email address; there is no separate user name. Django's auth
checks passwords (Argon2, config/settings.py); nothing here does.

The rules below hold for everything that goes through a model's save() or delete(); bulk
creates and updates of profiles refuse. QuerySet.update() and raw SQL bypass them: only
change_status uses update(), to write the status.
"""

import unicodedata

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models, transaction
from django.db.models import F, Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _


class UserManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        # As Django's login form normalises what is typed (NFKC), then the whole address in
        # lower case: it is the login, and people type it in any case.
        return unicodedata.normalize('NFKC', super().normalize_email(email)).lower()

    def get_by_natural_key(self, email):
        # Django's login looks the account up here, with the address as typed.
        return self.get(email=self.normalize_email(email))

    async def aget_by_natural_key(self, email):
        return await self.aget(email=self.normalize_email(email))

    def create_user(self, email, password=None, **fields):
        fields.setdefault('is_staff', False)
        fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **fields)

    def create_superuser(self, email, password=None, **fields):
        fields.setdefault('is_staff', True)
        fields.setdefault('is_superuser', True)
        return self._create_user(email, password, **fields)

    def _create_user(self, email, password, **fields):
        if not email:
            raise ValueError('An account needs an email address.')
        user = self.model(email=self.normalize_email(email), **fields)
        # With no password, Django stores an unusable one: nobody can log in with it.
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractUser):
    """A Django auth account (in place of Django's own auth_user), logged in by email address.

    Django's own fields, minus the user name. First and last name are the person's name.
    """

    username = None
    # Empty once the account is closed for good (a declined applicant, a revoked lawyer), so
    # that the address can be invited again as a new applicant (SPEC.md §5).
    email = models.EmailField(_('email address'), unique=True, null=True)
    # A revoked lawyer's address, kept so that the admin is warned before inviting it again
    # (SPEC.md §5). It never logs in. A declined applicant's is erased instead.
    former_email = models.EmailField(blank=True)

    USERNAME_FIELD = 'email'
    EMAIL_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        constraints = [
            # Stored as normalised, so that one address is never two accounts.
            models.CheckConstraint(condition=Q(email=Lower('email')), name='email_in_lower_case'),
            # The admin's account is both; every other account is neither.
            models.CheckConstraint(condition=Q(is_staff=F('is_superuser')), name='staff_only_with_superuser'),
        ]

    @property
    def is_admin(self):
        return self.is_staff

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email) or None
        if self.is_admin and LawyerProfile.objects.filter(user_id=self.pk).exists():
            raise ValueError("An applicant's or lawyer's account cannot also be the admin's.")
        super().save(*args, **kwargs)


class LawyerProfileQuerySet(models.QuerySet):
    def bulk_create(self, objs, *args, **kwargs):
        raise ValueError('Lawyer profiles are created one at a time, through save().')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError('Lawyer profiles are changed one at a time, through save().')

    def delete(self):
        raise ValueError('Lawyer profiles are never deleted.')


class LawyerProfile(models.Model):
    """What the archive knows about an applicant or lawyer, beyond their account.

    The status changes only through change_status, along STATUS_CHANGES; a revoked
    lawyer's or declined applicant's profile never changes again. Once someone is
    admitted, how they were verified and vouched for is fixed. Nothing here is ever
    deleted: a lawyer's audit entries and messages outlive their access (SPEC.md §5),
    so the account cannot be deleted either.
    """

    class Status(models.TextChoices):
        APPLICANT = 'applicant', _('Applicant')
        DECLINED = 'declined', _('Declined applicant')
        # Admitted and not suspended: a lawyer in the spec's sense (GLOSSARY.md).
        LAWYER = 'lawyer', _('Lawyer')
        SUSPENDED = 'suspended', _('Suspended lawyer')
        REVOKED = 'revoked', _('Revoked lawyer')

    STATUS_CHANGES = {
        Status.APPLICANT: {Status.LAWYER, Status.DECLINED},
        Status.DECLINED: set(),
        Status.LAWYER: {Status.SUSPENDED, Status.REVOKED},
        Status.SUSPENDED: {Status.LAWYER, Status.REVOKED},
        Status.REVOKED: set(),
    }
    FINAL_STATUSES = (Status.DECLINED, Status.REVOKED)
    # How someone was admitted (steps 1 and 2 of SPEC.md §5): fixed once they are.
    ADMISSION_FIELDS = ('verified_via', 'vouched_by')

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lawyer_profile',
    )
    bar = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLICANT)
    # Steps 1 and 2 of admission (SPEC.md §5), as the admin records them.
    verified_via = models.CharField(max_length=200, blank=True)
    vouched_by = models.CharField(max_length=200, blank=True)
    # The margin each new case starts with (SPEC.md §7): minutes before and after its time
    # window, and rings of the grid's finest cells around its area.
    default_margin_minutes = models.PositiveSmallIntegerField(default=10)
    default_margin_rings = models.PositiveSmallIntegerField(default=1)

    objects = LawyerProfileQuerySet.as_manager()

    class Meta:
        base_manager_name = 'objects'
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=['applicant', 'declined', 'lawyer', 'suspended', 'revoked']),
                name='known_status',
            ),
            models.CheckConstraint(
                condition=Q(status__in=['applicant', 'declined']) | (~Q(verified_via='') & ~Q(vouched_by='')),
                name='admitted_after_verification_and_vouch',
            ),
        ]

    def save(self, *args, **kwargs):
        if self.user.is_admin:
            raise ValueError("The admin's account cannot have a lawyer profile.")
        if self._state.adding:
            if self.status != self.Status.APPLICANT:
                raise ValueError('Every lawyer profile starts as an applicant.')
            # Always a new row: a profile given an existing id fails instead of replacing it.
            kwargs['force_insert'] = True
        else:
            self._refuse_forbidden_changes()
            # Only change_status writes the status, so that a save running at the same time
            # as a status change cannot write back the status it read.
            fields = kwargs.get('update_fields') or [
                field.name for field in self._meta.concrete_fields if not field.primary_key
            ]
            kwargs['update_fields'] = [field for field in fields if field != 'status']
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Lawyer profiles are never deleted.')

    def change_status(self, new_status):
        """Move to `new_status`, from the status stored now, not the one this copy was loaded with."""
        with transaction.atomic():
            stored = LawyerProfile.objects.select_for_update().values_list('status', flat=True).get(pk=self.pk)
            if new_status not in self.STATUS_CHANGES[stored]:
                raise ValueError(f'A lawyer profile cannot go from {stored} to {new_status}.')
            updated = LawyerProfile.objects.filter(pk=self.pk, status=stored).update(status=new_status)
            if updated != 1:
                raise ValueError('The lawyer profile changed while its status was being changed.')
        self.status = new_status

    def _refuse_forbidden_changes(self):
        stored = LawyerProfile.objects.filter(pk=self.pk).values(
            'user_id', 'status', *self.ADMISSION_FIELDS,
        ).first()
        if stored is None:
            return
        if stored['status'] in self.FINAL_STATUSES:
            raise ValueError("A revoked lawyer's or declined applicant's profile cannot be changed.")
        if self.user_id != stored['user_id']:
            raise ValueError('A lawyer profile cannot move to another account.')
        # Also refuses an out-of-date copy, whose status may have changed since it was loaded.
        if self.status != stored['status']:
            raise ValueError('The status changes only through change_status.')
        if stored['status'] != self.Status.APPLICANT and any(
            getattr(self, field) != stored[field] for field in self.ADMISSION_FIELDS
        ):
            raise ValueError("How a lawyer was verified and vouched for cannot be changed once they are admitted.")
