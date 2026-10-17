"""Logging in and out, and the page each person lands on (SPEC.md §5, §10)."""

from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache

from clips import rate_limits

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

    def form_valid(self, form):
        rate_limits.uncount(self.request, rate_limits.FAILED_LOGINS)
        rate_limits.uncount(
            self.request, rate_limits.FAILED_LOGINS_FOR_ACCOUNT, account=typed_account(self.request),
        )
        return super().form_valid(form)


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
        return render(request, 'accounts/applicant.html')
    return no_access(request)


@lawyer_required
def lawyer_home(request):
    return render(request, 'accounts/lawyer_home.html')


@admin_required
def manage_home(request):
    applicants = LawyerProfile.objects.filter(status=LawyerProfile.Status.APPLICANT).select_related('user').order_by(
        'user__last_name', 'user__first_name',
    )
    lawyers = LawyerProfile.objects.filter(
        status__in=[LawyerProfile.Status.LAWYER, LawyerProfile.Status.SUSPENDED],
    ).select_related('user').order_by('user__last_name', 'user__first_name')
    return render(request, 'accounts/manage_home.html', {'applicants': applicants, 'lawyers': lawyers})
