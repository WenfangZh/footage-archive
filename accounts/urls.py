from django.contrib.auth import views as auth_views
from django.urls import path

from alerts import views as alert_views
from cases import approval as case_approval
from cases import views as case_views

from . import access, admission, invitations, two_factor, views

app_name = 'accounts'

# The lawyer's pages and the admin's pages, under lawyer/ and manage/ (config/urls.py): every
# view there is wrapped in lawyer_required or admin_required (accounts.roles), as
# tests/test_roles.py checks.
lawyer_patterns = [
    path('', views.lawyer_home, name='home'),
    path('cases/new/', case_views.new_case, name='new_case'),
    path('cases/closed/', case_views.closed_cases, name='closed_cases'),
    path('cases/<int:case_id>/', case_views.case, name='case'),
    path('cases/<int:case_id>/close/', case_views.close_case, name='close_case'),
    path('margin/', case_views.default_margin, name='default_margin'),
]
manage_patterns = [
    path('', views.manage_home, name='home'),
    path('invite/', invitations.invite, name='invite'),
    path('applicants/<int:profile_id>/', admission.applicant, name='applicant'),
    path('applicants/<int:profile_id>/new-link/', invitations.send_new_link, name='send_new_link'),
    path('applicants/<int:profile_id>/decline/', admission.decline, name='decline'),
    path('lawyers/<int:profile_id>/suspend/', access.suspend, name='suspend'),
    path('lawyers/<int:profile_id>/reinstate/', access.reinstate, name='reinstate'),
    path('lawyers/<int:profile_id>/revoke/', access.revoke, name='revoke'),
    path('alerts/<int:alert_id>/seen/', alert_views.mark_seen, name='alert_seen'),
    path('cases/', case_approval.case_requests, name='case_requests'),
    path('cases/<int:case_id>/', case_approval.case_request, name='case_request'),
]

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    # Django's own; it only accepts POST.
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('account/', views.account_home, name='home'),
    path('account/two-factor/setup/', two_factor.setup, name='two_factor_setup'),
    path('account/two-factor/', two_factor.verify, name='two_factor_verify'),
    # Django's set-password page, reached from the link in an invitation.
    path('account/invitation/<uidb64>/<token>/', invitations.accept_invitation, name='accept_invitation'),
]
