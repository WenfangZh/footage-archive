"""Case labels, encrypted (docs/m5-encryption-design.md §7.4, §10).

The server encrypts a label when the case is created and seals its data key to the lawyer's
personal key; pages carry it encrypted, for the lawyer's browser to open. The admin never
sees it.
"""

import os

import pytest
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey

from accounts.models import LawyerProfile
from cases import labels
from cases.models import Case
from keys.grants import current_key
from tests.conftest import BASTILLE, log_in_with_second_factor, make_case, make_key, open_label

pytestmark = pytest.mark.django_db


def make_lawyer(email='lawyer@example.org', key=True):
    from django.contrib.auth import get_user_model
    profile = LawyerProfile.objects.create(user=get_user_model().objects.create_user(email), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    if key:
        profile.private_key = PrivateKey.generate()
        make_key(profile, profile.private_key)
    return profile


def dump():
    connection.ensure_connection()
    return '\n'.join(connection.connection.iterdump())


def test_a_label_is_stored_encrypted_and_opens_only_with_the_lawyer_s_key():
    lawyer = make_lawyer()
    case = make_case(lawyer, 'Bastille, 15 June')
    case.refresh_from_db()
    assert case.label == '' and case.label_key == current_key(lawyer)
    stored = dump()
    assert 'Bastille, 15 June' not in stored and 'Bastille, 15 June'.encode().hex() not in stored.lower()
    assert open_label(case, lawyer.private_key) == 'Bastille, 15 June'
    with pytest.raises(CryptoError):
        open_label(case, PrivateKey.generate())


def test_a_label_moved_to_another_case_does_not_open():
    lawyer = make_lawyer()
    first, second = make_case(lawyer, 'First'), make_case(lawyer, 'Second')
    first.refresh_from_db()
    # As a database writer could swap them: the encryption is bound to the case's id.
    second.label_ciphertext, second.label_sealed_key = first.label_ciphertext, first.label_sealed_key
    second.save(update_fields=['label_ciphertext', 'label_sealed_key'])
    with pytest.raises(CryptoError):
        open_label(second, lawyer.private_key)


def test_a_lawyer_without_a_personal_key_cannot_create_a_case():
    lawyer = make_lawyer(key=False)
    client = log_in_with_second_factor(Client(), lawyer.user)
    response = client.post(reverse('lawyer:new_case'), {
        'label': 'A case', 'starts_at': '2026-06-15T18:00', 'ends_at': '2026-06-15T19:00',
        'latitude': str(BASTILLE[0]), 'longitude': str(BASTILLE[1]), 'radius_metres': '50',
        'margin_minutes': '10', 'margin_rings': '1',
    })
    assert response.status_code == 400 and not Case.objects.exists()


def test_pages_carry_the_label_encrypted_with_the_case_number_until_the_browser_opens_it(settings):
    settings.LANGUAGE_CODE = 'en'
    lawyer = make_lawyer()
    case = make_case(lawyer, 'Bastille, 15 June')
    client = log_in_with_second_factor(Client(), lawyer.user)
    for url in (reverse('lawyer:home'), reverse('lawyer:case', kwargs={'case_id': case.pk})):
        page = client.get(url).content.decode()
        assert 'Bastille' not in page and f'Case no. {case.pk}' in page and 'data-label="{' in page
        assert 'id="unlock"' in page


def test_a_label_sealed_to_a_retired_key_is_unreadable(settings):
    settings.LANGUAGE_CODE = 'en'
    lawyer = make_lawyer()
    case = make_case(lawyer, 'Bastille, 15 June')
    current_key(lawyer).retire()
    make_key(lawyer)
    page = log_in_with_second_factor(Client(), lawyer.user).get(reverse('lawyer:home')).content.decode()
    assert f'Case no. {case.pk} (case label unreadable)' in page and 'data-label' not in page


def test_a_closed_case_can_have_its_label_sealed_but_nothing_else_changed():
    lawyer = make_lawyer()
    case = make_case(lawyer, 'Bastille, 15 June')
    Case.objects.filter(pk=case.pk).close(Case.ClosedBecause.LAWYER)
    case.refresh_from_db()
    labels.seal(case, 'Bastille, 15 June', current_key(lawyer))
    assert open_label(case, lawyer.private_key) == 'Bastille, 15 June'
    case.margin_minutes = 0
    with pytest.raises(ValueError):
        case.save()


def test_a_label_is_never_both_plaintext_and_encrypted():
    case = make_case(make_lawyer(), 'Bastille, 15 June')
    case.label = 'Bastille, 15 June'
    with pytest.raises(IntegrityError), transaction.atomic():
        case.save(update_fields=['label'])


@pytest.mark.django_db(transaction=True)
def test_encrypt_vault_seals_labels_from_before_encryption_to_their_lawyer_s_key():
    with_key, without_key = make_lawyer('one@example.org'), make_lawyer('two@example.org', key=False)
    old = make_case(with_key, 'Placeholder')
    make_key(without_key)
    keyless = make_case(without_key, 'Placeholder')
    current_key(without_key).retire()
    # As cases were stored before encryption: the plaintext label, nothing sealed.
    for case, label in ((old, 'Old label'), (keyless, 'Keyless label')):
        case.label, case.label_ciphertext, case.label_sealed_key, case.label_key, case.label_key_check = label, None, None, None, None
        case.save(update_fields=labels.LABEL_PARTS)
    call_command('encrypt_vault', stdout=open(os.devnull, 'w'))
    old.refresh_from_db()
    keyless.refresh_from_db()
    assert old.label == '' and open_label(old, with_key.private_key) == 'Old label'
    # Its lawyer has no key: left as it was, for when they have one.
    assert keyless.label == 'Keyless label' and keyless.label_ciphertext is None
    assert labels.encrypt_plaintext_labels() == (0, 1)



def test_a_label_is_sealed_only_to_its_own_lawyer_s_current_key():
    lawyer, other = make_lawyer(), make_lawyer('other@example.org')
    case = make_case(lawyer, 'Bastille, 15 June')
    with pytest.raises(ValueError):
        labels.seal(case, 'Bastille, 15 June', current_key(other))
    old = current_key(lawyer)
    old.retire()
    with pytest.raises(ValueError):
        labels.seal(case, 'Bastille, 15 June', old)


def test_error_reports_hide_the_label_in_the_form_and_where_it_is_sealed(monkeypatch, caplog):
    from django.views.debug import ExceptionReporter

    from cases.models import CaseCellQuerySet
    lawyer = make_lawyer()
    client = log_in_with_second_factor(Client(raise_request_exception=False), lawyer.user)

    def fails(*args, **kwargs):
        raise RuntimeError('the database went away')

    monkeypatch.setattr(CaseCellQuerySet, 'bulk_create', fails)
    response = client.post(reverse('lawyer:new_case'), {
        'label': 'SecretLabelXYZ', 'starts_at': '2026-06-15T18:00', 'ends_at': '2026-06-15T19:00',
        'latitude': str(BASTILLE[0]), 'longitude': str(BASTILLE[1]), 'radius_metres': '50',
        'margin_minutes': '10', 'margin_rings': '1',
    })
    assert response.status_code == 500 and not Case.objects.exists()
    [record] = [record for record in caplog.records if record.name == 'django.request']
    data = ExceptionReporter(record.request, *record.exc_info).get_traceback_data()
    assert 'SecretLabelXYZ' not in str(data['filtered_POST_items'])
    [frame] = [frame for frame in data['frames'] if frame['function'] == 'create_with_area']
    assert 'SecretLabelXYZ' not in str(frame['vars'])
