"""Messages in the vault: encrypted, opened only with the upload code or the thread's lawyer's
personal key (SPEC.md §6, §7; docs/m5-encryption-design.md §7.3)."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from nacl import bindings
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, SealedBox

from accounts.models import LawyerProfile
from clips import vault
from clips.editing import delete_clip
from clips.models import Clip, Message
from clips.time_blocks import four_hour_block
from tests.conftest import TEST_CODE, make_key, with_code_holder_key

pytestmark = pytest.mark.django_db

TO = Message.Direction.TO_CODE_HOLDER
FROM = Message.Direction.FROM_CODE_HOLDER
TEXT = 'Could you tell me what happened just before the start of your video?'


def make_lawyer(email, key=True):
    """An admitted lawyer, with a personal key the admin confirmed unless `key` is False."""
    profile = LawyerProfile.objects.create(user=get_user_model().objects.create_user(email), bar='Paris')
    profile.verified_via, profile.vouched_by = 'The bar directory', 'A defence organisation'
    profile.save()
    profile.change_status(LawyerProfile.Status.LAWYER)
    private_key = PrivateKey.generate() if key else None
    if key:
        confirmed_key(profile, private_key)
    return profile, private_key


def confirmed_key(profile, private_key):
    lawyer_key = make_key(profile, private_key)
    lawyer_key.admin_confirmed_at = timezone.now()
    lawyer_key.save()
    return lawyer_key


@pytest.fixture
def lawyer():
    return make_lawyer('lawyer@example.org')


@pytest.fixture
def clip():
    return with_code_holder_key(Clip.objects.create(code_hash='0' * 64))


def opened_by_lawyer(message, private_key):
    """The text as the lawyer's browser opens it, from what the page is given."""
    sealed = vault.sealed_message(message)
    data_key = SealedBox(private_key).decrypt(sealed['sealed_key'])
    encrypted = sealed['ciphertext']
    padded = bindings.crypto_aead_xchacha20poly1305_ietf_decrypt(
        encrypted[vault.NONCE_BYTES:], sealed['additional_data'], encrypted[:vault.NONCE_BYTES], data_key,
    )
    return bindings.sodium_unpad(padded, vault.TEXT_BLOCK).decode()


def test_the_code_holder_and_the_lawyer_both_read_a_message_and_a_reply(clip, lawyer):
    profile, private_key = lawyer
    sent = vault.send_message(clip, profile, TO, TEXT)
    reply = vault.send_message(clip, profile, FROM, 'Yes: they were pushed first.')
    assert [(message.pk, text) for message, text in vault.code_holder_messages(clip, TEST_CODE)] == [
        (sent.pk, TEXT), (reply.pk, 'Yes: they were pushed first.'),
    ]
    assert opened_by_lawyer(sent, private_key) == TEXT
    assert opened_by_lawyer(reply, private_key) == 'Yes: they were pushed first.'


def test_no_text_is_stored_in_plaintext(clip, lawyer):
    vault.send_message(clip, lawyer[0], TO, TEXT)
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM clips_message')
        rows = cursor.fetchall()
    stored = b''.join(value if isinstance(value, bytes) else str(value).encode() for row in rows for value in row)
    assert TEXT.encode() not in stored and b'happened' not in stored


def test_the_length_shows_only_to_the_nearest_block(clip, lawyer):
    short = vault.send_message(clip, lawyer[0], TO, 'Hello')
    longer = vault.send_message(clip, lawyer[0], TO, 'Hello' * 40)
    assert len(bytes(short.ciphertext)) == len(bytes(longer.ciphertext))


def test_no_other_key_opens_it(clip, lawyer):
    message = vault.send_message(clip, lawyer[0], TO, TEXT)
    other_profile, other_key = make_lawyer('other@example.org')
    group_key = PrivateKey.generate()
    for key in (other_key, group_key):
        with pytest.raises(CryptoError):
            SealedBox(key).decrypt(bytes(message.lawyer_sealed_key))
        with pytest.raises(CryptoError):
            SealedBox(key).decrypt(bytes(message.code_holder_sealed_key))
    with pytest.raises(vault.WrongCode):
        vault.code_holder_messages(clip, '0' * len(TEST_CODE))


def test_a_text_moved_to_another_message_does_not_open(clip, lawyer):
    profile, private_key = lawyer
    first = vault.send_message(clip, profile, TO, TEXT)
    second = vault.send_message(clip, profile, TO, 'Another')
    for column in ('ciphertext', 'code_holder_sealed_key', 'lawyer_sealed_key', 'key_check'):
        setattr(second, column, getattr(first, column))
    second.save()
    with pytest.raises(CryptoError):
        opened_by_lawyer(second, private_key)
    # Only the altered message is hidden from the code holder.
    assert [text for _, text in vault.code_holder_messages(clip, TEST_CODE)] == [TEXT, None]


@pytest.mark.parametrize('change', [
    {'direction': FROM}, {'kind': Message.Kind.WRONG_TIME}, {'identified': True}, {'thread': True},
])
def test_a_text_whose_thread_direction_kind_or_naming_changes_does_not_open(clip, lawyer, change):
    message = vault.send_message(clip, lawyer[0], TO, TEXT)
    if change.pop('thread', False):
        change = {'lawyer_profile': make_lawyer('other@example.org')[0]}
    Message.objects.filter(pk=message.pk).update(**change)
    assert vault.code_holder_messages(clip, TEST_CODE)[0][1] is None


def test_a_report_without_a_note_holds_nothing_encrypted(clip, lawyer):
    report = vault.send_message(clip, lawyer[0], TO, '', kind=Message.Kind.WRONG_PLACE)
    assert report.ciphertext is None and report.lawyer_key is None
    assert vault.code_holder_messages(clip, TEST_CODE) == [(report, '')]
    assert vault.sealed_message(report) is None


def test_a_plain_message_always_has_a_text(clip, lawyer):
    with pytest.raises(IntegrityError), transaction.atomic():
        vault.send_message(clip, lawyer[0], TO, '')


@pytest.mark.parametrize('fields', [{'kind': Message.Kind.WRONG_TIME}, {'identified': True}])
def test_only_lawyers_report_or_are_named(clip, lawyer, fields):
    with pytest.raises(IntegrityError), transaction.atomic():
        vault.send_message(clip, lawyer[0], FROM, 'A note', **fields)


def test_messages_take_their_places_in_each_thread(clip, lawyer):
    other = make_lawyer('other@example.org')[0]
    places = [
        (vault.send_message(clip, profile, TO, TEXT).position, profile)
        for profile in (lawyer[0], lawyer[0], other, lawyer[0])
    ]
    assert places == [(0, lawyer[0]), (1, lawyer[0]), (0, other), (2, lawyer[0])]


def test_the_time_is_kept_to_its_four_hour_block(clip, lawyer):
    block = four_hour_block(timezone.now())
    message = vault.send_message(clip, lawyer[0], TO, TEXT)
    message.refresh_from_db()
    assert message.sent_at == block


def test_nothing_is_sent_without_a_lawyer_key_the_admin_confirmed_or_a_code_holder_key(clip):
    keyless = make_lawyer('keyless@example.org', key=False)[0]
    for direction in (TO, FROM):
        with pytest.raises(vault.NoLawyerKey):
            vault.send_message(clip, keyless, direction, TEXT)
    # As after someone took over the account and reset its key (design §5.6).
    make_key(keyless, PrivateKey.generate())
    for direction in (TO, FROM):
        with pytest.raises(vault.NoLawyerKey):
            vault.send_message(clip, keyless, direction, TEXT)
    profile, _ = make_lawyer('lawyer@example.org')
    old_clip = Clip.objects.create(code_hash='1' * 64)
    with pytest.raises(vault.NoCodeHolderKey):
        vault.send_message(old_clip, profile, TO, TEXT)
    assert not Message.objects.exists()


def test_after_a_key_reset_only_the_code_holder_still_reads_it(clip, lawyer):
    profile, _ = lawyer
    message = vault.send_message(clip, profile, TO, TEXT)
    message.lawyer_key.retire()
    message.refresh_from_db()
    assert vault.sealed_message(message) is None
    assert vault.code_holder_messages(clip, TEST_CODE)[0][1] == TEXT


def test_a_message_is_sealed_to_the_lawyers_current_key(clip, lawyer):
    profile, _ = lawyer
    profile_key = vault.send_message(clip, profile, TO, TEXT).lawyer_key
    profile_key.retire()
    new_private_key = PrivateKey.generate()
    new_key = confirmed_key(profile, new_private_key)
    message = vault.send_message(clip, profile, TO, 'After the new key')
    assert message.lawyer_key == new_key
    assert opened_by_lawyer(message, new_private_key) == 'After the new key'


@pytest.mark.parametrize('status', [LawyerProfile.Status.SUSPENDED, LawyerProfile.Status.REVOKED])
def test_nothing_is_sent_to_or_from_a_suspended_or_revoked_lawyer(clip, lawyer, status):
    profile, _ = lawyer
    profile.change_status(status)
    for direction in (TO, FROM):
        with pytest.raises(vault.NotALawyer):
            vault.send_message(clip, profile, direction, TEXT)
    assert not Message.objects.exists()


def test_the_database_refuses_an_unknown_direction_or_kind_and_half_encrypted_text(clip, lawyer):
    message = vault.send_message(clip, lawyer[0], TO, TEXT)
    for change in [{'direction': 'sideways'}, {'kind': 'bogus', 'ciphertext': None, 'code_holder_sealed_key': None,
                                                 'lawyer_sealed_key': None, 'lawyer_key': None, 'key_check': None},
                   {'lawyer_sealed_key': None}]:
        with pytest.raises(IntegrityError), transaction.atomic():
            Message.objects.filter(pk=message.pk).update(**change)


def test_deleting_the_clip_deletes_its_messages(clip, lawyer):
    vault.send_message(clip, lawyer[0], TO, TEXT)
    delete_clip(clip)
    assert not Message.objects.exists()
