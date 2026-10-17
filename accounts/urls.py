from django.contrib.auth import views as auth_views
from django.urls import path

from . import invitations, two_factor, views

app_name = 'accounts'

# The lawyer's pages and the admin's pages, under lawyer/ and manage/ (config/urls.py): every
# view there is wrapped in lawyer_required or admin_required (accounts.roles), as
# tests/test_roles.py checks.
lawyer_patterns = [
    path('', views.lawyer_home, name='home'),
]
manage_patterns = [
    path('', views.manage_home, name='home'),
    path('invite/', invitations.invite, name='invite'),
    path('applicants/<int:profile_id>/new-link/', invitations.send_new_link, name='send_new_link'),
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
