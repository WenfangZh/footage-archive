from django.urls import path

from alerts import views as alert_views
from cases import approval as case_approval
from cases import clip_pages
from cases import views as case_views
from keys import views as key_views
from maps import views as map_views

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
    path('cases/<int:case_id>/search/', case_views.search, name='search'),
    path('cases/<int:case_id>/clips/<uuid:clip_id>/', clip_pages.clip_page, name='clip'),
    path('cases/<int:case_id>/clips/<uuid:clip_id>/preview/', clip_pages.preview, name='preview'),
    path('cases/<int:case_id>/clips/<uuid:clip_id>/download/', clip_pages.download, name='download'),
    # Under the clip's own path: the data key cookie it sets is sent nowhere else (cases.clip_pages).
    path('cases/<int:case_id>/clips/<uuid:clip_id>/data-key/<str:file>/', clip_pages.data_key, name='data_key'),
    path('cases/<int:case_id>/clips/<uuid:clip_id>/hash-record/', clip_pages.hash_record, name='hash_record'),
    path('margin/', case_views.default_margin, name='default_margin'),
    # Handing over the group key (keys.views).
    path('keys/', key_views.waiting, name='waiting'),
    path('keys/<int:lawyer_key_id>/', key_views.grant, name='grant'),
    # Rotations of the group key, for the rotator's and checkers' browsers (keys.views).
    path('rotation/role.json', key_views.rotation_role, name='rotation_role'),
    path('rotation/new-key/', key_views.rotation_new_key, name='rotation_new_key'),
    path('rotation/reseal.json', key_views.rotation_reseal, name='rotation_reseal'),
    path('rotation/resealed/', key_views.rotation_resealed, name='rotation_resealed'),
    path('rotation/unopenable/', key_views.rotation_unopenable, name='rotation_unopenable'),
    path('rotation/check.json', key_views.rotation_to_check, name='rotation_to_check'),
    path('rotation/checked/', key_views.rotation_checked, name='rotation_checked'),
    # The map's data, never logged (maps.apps): keep every route of it under map/.
    path('map/style.json', map_views.style, name='map_style'),
    path('map/tiles.pmtiles', map_views.tiles, name='map_tiles'),
    path('map/fonts/<str:fontstack>/<str:glyph_range>.pbf', map_views.glyphs, name='map_glyphs'),
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
    path('lawyers/<int:profile_id>/key/', key_views.confirm_key, name='confirm_key'),
    path('alerts/<int:alert_id>/seen/', alert_views.mark_seen, name='alert_seen'),
    path('lawyers/<int:profile_id>/alert-limit/', alert_views.raise_limit, name='raise_limit'),
    path('cases/', case_approval.case_requests, name='case_requests'),
    path('cases/<int:case_id>/', case_approval.case_request, name='case_request'),
]

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    # Django's own, which only accepts POST, telling the browser to forget what this site stored.
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('account/', views.account_home, name='home'),
    path('account/two-factor/setup/', two_factor.setup, name='two_factor_setup'),
    path('account/two-factor/', two_factor.verify, name='two_factor_verify'),
    # A personal key (keys.views): set up by applicants, unlocked by applicants and lawyers.
    # tests/test_personal_keys.py checks who reaches them.
    path('account/key/', key_views.key_page, name='key'),
    path('account/key/setup/', key_views.setup, name='key_setup'),
    path('account/key/recover/', key_views.recover, name='key_recover'),
    path('account/key/reset/', key_views.reset, name='key_reset'),
    path('account/key/encrypted.json', key_views.encrypted_key, name='encrypted_key'),
    path('account/key/unlocked/', key_views.unlocked, name='key_unlocked'),
    path('account/key/grants.json', key_views.own_grants, name='own_grants'),
    path('account/key/grant-checked/', key_views.grant_checked, name='grant_checked'),
    path('account/key/group-key/', key_views.start_group_key, name='start_group_key'),
    # Django's set-password page, reached from the link in an invitation.
    path('account/invitation/<uidb64>/<token>/', invitations.accept_invitation, name='accept_invitation'),
]
