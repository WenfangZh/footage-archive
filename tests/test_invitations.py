import datetime
import re
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import Client
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import LawyerProfile
from audit.models import Action, AuditEntry
from tests.conftest import log_in_with_second_factor
from tests.test_roles import SHUT_OUT, make_account

pytestmark = pytest.mark.django_db

User = get_user_model()
NEW_PASSWORD = 'a long new passphrase for the archive'
INVITATION = {
    'email': 'Invited@Example.org', 'first_name': 'Camille', 'last_name': 'Exemple', 'bar': 'Paris',
}


@pytest.fixture
def admin_client():
    return log_in_with_second_factor(Client(), User.objects.create_superuser('admin@example.org'))


@pytest.fixture
def invited_client():
    # The invited person's own browser.
    return Client()


def invite(admin_client, **changes):
    return admin_client.post(reverse('manage:invite'), {**INVITATION, **changes})


def link_in(message):
    return re.search(r'http://testserver(/account/invitation/\S+/)', message.body).group(1)


def link_for(user):
    return reverse('accounts:accept_invitation', kwargs={
        'uidb64': urlsafe_base64_encode(force_bytes(user.pk)),
        'token': default_token_generator.make_token(user),
    })


def accept(client, link, password=NEW_PASSWORD):
    page = client.get(link, follow=True)
    if not page.context['validlink']:
        return page
    return client.post(page.redirect_chain[-1][0], {'new_password1': password, 'new_password2': password})


def new_link(admin_client):
    return admin_client.post(reverse('manage:send_new_link', args=[LawyerProfile.objects.get().pk]))


def test_inviting_makes_an_applicant_with_no_password_and_an_audit_entry(admin_client):
    response = invite(admin_client)
    # Back to a page that a reload does not send again.
    assert response.status_code == 302 and response['Location'] == reverse('manage:home')
    user = User.objects.get(email='invited@example.org')
    assert not user.has_usable_password()
    assert (user.first_name, user.last_name) == ('Camille', 'Exemple')
    profile = user.lawyer_profile
    assert profile.status == LawyerProfile.Status.APPLICANT and profile.bar == 'Paris'
    entry = AuditEntry.objects.get()
    assert entry.action == Action.INVITATION and entry.lawyer_profile == profile
    assert entry.admin.email == 'admin@example.org'
    assert 'invited@example.org' in admin_client.get(reverse('manage:home')).content.decode()


def test_the_email_is_in_french_whatever_the_admin_s_language_with_a_link_and_nothing_else(admin_client):
    admin_client.cookies['django_language'] = 'en'
    invite(admin_client)
    [message] = mail.outbox
    assert message.to == ['invited@example.org']
    assert 'Définissez votre mot de passe' in message.body
    assert link_in(message)
    # Nothing about the person or the archive's contents (SPEC.md §11).
    assert 'Camille' not in message.body and 'Paris' not in message.body


def test_an_address_already_used_is_refused_with_a_message(admin_client):
    User.objects.create_user('invited@example.org')
    response = invite(admin_client)
    assert response.status_code == 200 and not mail.outbox
    assert 'Un compte utilise déjà cette adresse.' in response.content.decode()
    assert User.objects.filter(email='invited@example.org').count() == 1


def test_if_the_email_cannot_be_sent_no_account_is_kept(admin_client):
    with mock.patch('accounts.invitations.send_mail', side_effect=OSError('no mail server')):
        with pytest.raises(OSError):
            invite(admin_client)
    assert not User.objects.filter(email='invited@example.org').exists()
    assert not AuditEntry.objects.exists()


def test_the_link_sets_the_password_and_leads_to_the_second_factor(admin_client, invited_client):
    invite(admin_client)
    response = accept(invited_client, link_in(mail.outbox[0]))
    assert response.status_code == 302
    assert User.objects.get(email='invited@example.org').check_password(NEW_PASSWORD)
    home = invited_client.get(reverse('accounts:home'))
    assert home['Location'].startswith(reverse('accounts:two_factor_setup'))


def test_the_link_works_only_once(admin_client, invited_client):
    invite(admin_client)
    link = link_in(mail.outbox[0])
    accept(invited_client, link)
    invited_client.logout()
    assert not accept(invited_client, link, 'another long passphrase here').context['validlink']
    assert User.objects.get(email='invited@example.org').check_password(NEW_PASSWORD)


def test_the_link_works_until_it_expires(admin_client, invited_client, settings):
    invite(admin_client)
    link = link_in(mail.outbox[0])
    days = datetime.timedelta(days=settings.INVITATION_DAYS)
    generator = type(default_token_generator)
    with mock.patch.object(generator, '_now', return_value=datetime.datetime.now() + days + datetime.timedelta(minutes=1)):
        assert not accept(invited_client, link).context['validlink']
    with mock.patch.object(generator, '_now', return_value=datetime.datetime.now() + days - datetime.timedelta(minutes=1)):
        assert accept(invited_client, link).status_code == 302


def test_a_new_link_makes_earlier_ones_stop_working(admin_client, invited_client):
    invite(admin_client)
    first = link_in(mail.outbox[0])
    assert new_link(admin_client).status_code == 302
    second = link_in(mail.outbox[1])
    assert AuditEntry.objects.filter(action=Action.INVITATION).count() == 2
    assert not accept(invited_client, first).context['validlink']
    assert accept(invited_client, second).status_code == 302


def test_a_new_link_is_never_sent_once_the_password_is_set(admin_client, invited_client):
    invite(admin_client)
    accept(invited_client, link_in(mail.outbox[0]))
    assert new_link(admin_client).status_code == 404
    assert len(mail.outbox) == 1
    assert User.objects.get(email='invited@example.org').check_password(NEW_PASSWORD)
    assert 'Envoyer un nouveau lien' not in admin_client.get(reverse('manage:home')).content.decode()


def test_if_a_new_link_cannot_be_sent_the_old_one_still_works(admin_client, invited_client):
    invite(admin_client)
    first = link_in(mail.outbox[0])
    with mock.patch('accounts.invitations.send_mail', side_effect=OSError('no mail server')):
        with pytest.raises(OSError):
            new_link(admin_client)
    assert accept(invited_client, first).status_code == 302


def test_a_new_link_is_only_for_applicants(admin_client):
    invite(admin_client)
    LawyerProfile.objects.get().change_status(LawyerProfile.Status.DECLINED)
    assert new_link(admin_client).status_code == 404
    assert len(mail.outbox) == 1


@pytest.mark.parametrize('kind', [kind for kind in SHUT_OUT if kind != 'applicant'] + ['lawyer', 'admin'])
def test_a_link_never_works_for_someone_who_is_not_an_applicant(invited_client, kind):
    # Django's token machinery is the same for any account; the page refuses all but applicants.
    assert not accept(invited_client, link_for(make_account(kind))).context['validlink']
