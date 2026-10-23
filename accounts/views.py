"""Logging in and out, and the page each person lands on (SPEC.md §5, §10)."""

from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache

from alerts.checks import failed_login
from alerts.models import Alert
from cases.models import Case
from clips import rate_limits
from keys.models import LawyerKey

from .models import LawyerProfile, User
from . import two_factor
from .roles import Role, admin_required, lawyer_required, no_access, role


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        # Django's own says both fields may be case-sensitive; the email address is not.
        'invalid_login': _('Please enter a correct email address and password. The password is case-sensitive.'),
    }


class LoginView(auth_views.LoginView):
    """Django's login, refused outright once a connection or an account has failed too often.

    Every attempt is counted before any password is checked, so that attempts sent all at
    once cannot overrun the limit; a successful one is taken back. Over a limit, no
    password is checked at all, not even a right one, until the window ends. Checking
    passwords stays Django's job.
    """

    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        account = typed_account(request)
        for_connection = rate_limits.count(request, rate_limits.FAILED_LOGINS)
        for_account = rate_limits.count(request, rate_limits.FAILED_LOGINS_FOR_ACCOUNT, account=account)
        if (for_connection > rate_limits.limit(rate_limits.FAILED_LOGINS)
                or for_account > rate_limits.limit(rate_limits.FAILED_LOGINS_FOR_ACCOUNT)):
            return rate_limits.too_many(request, 'accounts/login_limited.html')
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        failed_login(self.request, typed_account(self.request))
        return super().form_invalid(form)

    def form_valid(self, form):
        rate_limits.uncount(self.request, rate_limits.FAILED_LOGINS)
        rate_limits.uncount(
            self.request, rate_limits.FAILED_LOGINS_FOR_ACCOUNT, account=typed_account(self.request),
        )
        return super().form_valid(form)


class LogoutView(auth_views.LogoutView):
    """Django's, which only accepts POST, and which also tells the browser to forget what
    this site stored in it: keys unlocked in a tab live there (docs/m5-encryption-design.md §4)."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        response['Clear-Site-Data'] = '"storage"'
        return response


def typed_account(request):
    # The address as the login will look it up, so that its case makes no difference.
    # Anything longer than an address can be is not normalised (as Django's login form).
    typed = request.POST.get('username', '')
    return User.objects.normalize_email(typed) if len(typed) <= 254 else 'too long'


@never_cache
@login_required
def account_home(request):
    current = role(request.user)
    if current == Role.NO_ACCESS:
        return no_access(request)
    # Applicants too set up their second factor, before admission.
    if not request.user.is_verified():
        return two_factor.required_step(request)
    if current == Role.ADMIN:
        return redirect('manage:home')
    if current == Role.LAWYER:
        return redirect('lawyer:home')
    if current == Role.APPLICANT:
        has_key = LawyerKey.objects.current().filter(lawyer_profile__user=request.user).exists()
        return render(request, 'accounts/applicant.html', {'has_key': has_key})
    return no_access(request)


@lawyer_required
def lawyer_home(request):
    cases = Case.objects.filter(lawyer_profile__user=request.user).open().order_by('-opened_at')
    return render(request, 'accounts/lawyer_home.html', {'cases': cases})


@admin_required
def manage_home(request):
    applicants = LawyerProfile.objects.filter(status=LawyerProfile.Status.APPLICANT).select_related('user').order_by(
        'user__last_name', 'user__first_name',
    )
    lawyers = LawyerProfile.objects.filter(
        status__in=[LawyerProfile.Status.LAWYER, LawyerProfile.Status.SUSPENDED],
    ).select_related('user').order_by('user__last_name', 'user__first_name')
    alerts = Alert.objects.filter(seen_at__isnull=True).select_related('account').order_by('-raised_at')
    return render(request, 'accounts/manage_home.html', {'alerts': alerts, 'applicants': applicants, 'lawyers': lawyers})
