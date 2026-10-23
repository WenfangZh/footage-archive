"""PyNaCl on the server and libsodium.js in browsers must agree (ADR 0007).

Values made by libsodium.js (tests/vectors/libsodium_js.json) must open or be reproduced
with PyNaCl. The other direction is checked by tests/vectors/make_libsodium_js.mjs, which
needs Node.js and so does not run here; see tests/vectors/README.md.
"""

import hashlib
import json
from pathlib import Path

import nacl.bindings as b
import nacl.exceptions
import nacl.pwhash
import pytest
from nacl.encoding import RawEncoder
from nacl.hash import blake2b
from nacl.public import PrivateKey, SealedBox

from keys.models import safety_number

VECTORS = Path(__file__).parent / 'vectors'


def load(name):
    data = json.loads((VECTORS / name).read_text())

    def unhex(value):
        if isinstance(value, dict):
            return {key: unhex(item) for key, item in value.items()}
        if isinstance(value, list):
            return [unhex(item) for item in value]
        return bytes.fromhex(value) if isinstance(value, str) else value

    skipped = {'made_by', 'checked_pynacl_sha256', 'safety_number'}
    return {key: unhex(value) for key, value in data.items() if key not in skipped}


@pytest.fixture(params=['libsodium_js.json', 'pynacl.json'])
def v(request):
    return load(request.param)


@pytest.mark.parametrize('name, maker', [('libsodium_js.json', 'libsodium.js '), ('pynacl.json', 'PyNaCl ')])
def test_each_file_was_made_by_its_own_library(name, maker):
    assert json.loads((VECTORS / name).read_text())['made_by'].startswith(maker)


def test_libsodium_js_checked_the_current_pynacl_values():
    # The Node script records which pynacl.json it opened: PyNaCl's values rewritten since
    # then have not been checked in a browser's library (README.md).
    checked = json.loads((VECTORS / 'libsodium_js.json').read_text())['checked_pynacl_sha256']
    assert checked == hashlib.sha256((VECTORS / 'pynacl.json').read_bytes()).hexdigest()


def test_seeded_key_pair(v):
    seeded = v['seed_keypair']
    assert bytes(PrivateKey.from_seed(seeded['seed']).public_key) == seeded['public_key']


def test_public_key_from_private_key(v):
    keys = v['scalarmult_base']
    assert b.crypto_scalarmult_base(keys['private_key']) == keys['public_key']


def test_sealed_box_opens(v):
    sealed = v['sealed_box']
    recipient = PrivateKey.from_seed(sealed['recipient_seed'])
    assert SealedBox(recipient).decrypt(sealed['ciphertext']) == sealed['message']


def test_authenticated_encryption(v):
    aead = v['aead']
    assert b.crypto_aead_xchacha20poly1305_ietf_encrypt(aead['message'], aead['ad'], aead['nonce'], aead['key']) == aead['ciphertext']
    assert b.crypto_aead_xchacha20poly1305_ietf_decrypt(aead['ciphertext'], aead['ad'], aead['nonce'], aead['key']) == aead['message']


def test_secretstream_opens_in_order_with_its_tags(v):
    stream = v['secretstream']
    state = b.crypto_secretstream_xchacha20poly1305_state()
    b.crypto_secretstream_xchacha20poly1305_init_pull(state, stream['header'], stream['key'])
    for chunk in stream['chunks']:
        assert b.crypto_secretstream_xchacha20poly1305_pull(state, chunk['ciphertext'], stream['ad']) == (chunk['message'], chunk['tag'])
    assert stream['chunks'][-1]['tag'] == b.crypto_secretstream_xchacha20poly1305_TAG_FINAL


def test_secretstream_refuses_other_additional_data(v):
    stream = v['secretstream']
    state = b.crypto_secretstream_xchacha20poly1305_state()
    b.crypto_secretstream_xchacha20poly1305_init_pull(state, stream['header'], stream['key'])
    with pytest.raises(nacl.exceptions.CryptoError):
        b.crypto_secretstream_xchacha20poly1305_pull(state, stream['chunks'][0]['ciphertext'], b'other-clip|proxy')


def test_argon2id(v):
    argon = v['argon2id']
    # The limits lawyers' browsers use (docs/m5-encryption-design.md §4).
    assert (argon['opslimit'], argon['memlimit']) == (nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE, nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE)
    derived = nacl.pwhash.argon2id.kdf(32, argon['passphrase'], argon['salt'], opslimit=argon['opslimit'], memlimit=argon['memlimit'])
    assert derived == argon['key']


def test_blake2b_with_a_label(v):
    hashed = v['blake2b']
    assert len(hashed['digest']) == 32
    assert blake2b(hashed['label'] + hashed['input'], digest_size=32, encoder=RawEncoder) == hashed['digest']


def test_keyed_blake2b(v):
    keyed = v['blake2b_keyed']
    assert blake2b(keyed['label'], digest_size=32, key=keyed['key'], encoder=RawEncoder) == keyed['digest']


def test_the_browser_and_the_server_compute_the_same_safety_number():
    # Only libsodium.js's file has it: keys/static/keys/keys.js computed it.
    vector = json.loads((VECTORS / 'libsodium_js.json').read_text())['safety_number']
    assert safety_number(bytes.fromhex(vector['public_key'])) == vector['number']


def test_padding(v):
    pad = v['pad']
    assert pad['block_size'] == 256
    assert b.sodium_pad(pad['message'], pad['block_size']) == pad['padded']
    assert b.sodium_unpad(pad['padded'], pad['block_size']) == pad['message']
