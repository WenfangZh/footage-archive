"""PyNaCl on the server and libsodium.js in browsers must agree (ADR 0007).

Values made by libsodium.js (tests/vectors/libsodium_js.json) must open or be reproduced
with PyNaCl. The other direction is checked by tests/vectors/make_libsodium_js.mjs, which
needs Node.js and so does not run here; see tests/vectors/README.md.
"""

import json
from pathlib import Path

import nacl.bindings as b
import nacl.exceptions
import nacl.pwhash
import pytest
from nacl.encoding import RawEncoder
from nacl.hash import blake2b
from nacl.public import PrivateKey, SealedBox

VECTORS = Path(__file__).parent / 'vectors'


def load(name):
    data = json.loads((VECTORS / name).read_text())

    def unhex(value):
        if isinstance(value, dict):
            return {key: unhex(item) for key, item in value.items()}
        if isinstance(value, list):
            return [unhex(item) for item in value]
        return bytes.fromhex(value) if isinstance(value, str) else value

    return {key: unhex(value) for key, value in data.items() if key != 'made_by'}


@pytest.fixture(params=['libsodium_js.json', 'pynacl.json'])
def v(request):
    return load(request.param)


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
    derived = nacl.pwhash.argon2id.kdf(32, argon['passphrase'], argon['salt'], opslimit=argon['opslimit'], memlimit=argon['memlimit'])
    assert derived == argon['key']


def test_blake2b_with_a_label(v):
    hashed = v['blake2b']
    assert blake2b(hashed['label'] + hashed['input'], digest_size=32, encoder=RawEncoder) == hashed['digest']


def test_keyed_blake2b(v):
    keyed = v['blake2b_keyed']
    assert blake2b(keyed['label'], digest_size=32, key=keyed['key'], encoder=RawEncoder) == keyed['digest']


def test_padding(v):
    pad = v['pad']
    assert b.sodium_pad(pad['message'], pad['block_size']) == pad['padded']
    assert b.sodium_unpad(pad['padded'], pad['block_size']) == pad['message']
