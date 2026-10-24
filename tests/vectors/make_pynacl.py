"""Writes tests/vectors/pynacl.json: values made by PyNaCl, for libsodium.js to open (README.md).

Run from the repository root: python tests/vectors/make_pynacl.py
Sealed boxes and secretstream headers are random, so each run writes different values;
run it only when the vectors change.
"""

import json
import os
from pathlib import Path

import nacl
import nacl.bindings as b
import nacl.pwhash
from nacl.encoding import RawEncoder
from nacl.hash import blake2b
from nacl.public import PrivateKey, SealedBox

HERE = Path(__file__).parent


def pattern(length, start):
    """Fixed bytes, so that inputs are the same on every run."""
    return bytes((start + i) % 256 for i in range(length))


def make():
    seed = pattern(32, 1)
    private = PrivateKey.from_seed(seed)
    recipient_seed = pattern(32, 2)
    recipient = PrivateKey.from_seed(recipient_seed)
    sealed_message = b'a data key, sealed to the group key'

    aead_key, nonce = pattern(32, 3), pattern(24, 4)
    ad, aead_message = b'clip-id|context', b'context typed by the uploader'

    stream_key, stream_ad = pattern(32, 5), b'clip-id|proxy'
    state = b.crypto_secretstream_xchacha20poly1305_state()
    header = b.crypto_secretstream_xchacha20poly1305_init_push(state, stream_key)
    chunks = []
    for message, tag in [
        (b'first chunk', b.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE),
        (b'last chunk', b.crypto_secretstream_xchacha20poly1305_TAG_FINAL),
    ]:
        ciphertext = b.crypto_secretstream_xchacha20poly1305_push(state, message, stream_ad, tag)
        chunks.append({'message': message.hex(), 'tag': tag, 'ciphertext': ciphertext.hex()})

    passphrase, salt = b'ABCD-EFGH-JKMN-PQRS-TVWX', pattern(16, 6)
    opslimit = nacl.pwhash.argon2id.OPSLIMIT_INTERACTIVE
    memlimit = nacl.pwhash.argon2id.MEMLIMIT_INTERACTIVE

    label, label_input = b'fa-safetynum-v1', pattern(32, 7)
    check_key, check_label = pattern(32, 8), b'fa-keycheck-v1'
    padded_message = b'contact details'

    return {
        'made_by': f'PyNaCl {nacl.__version__}',
        'seed_keypair': {'seed': seed.hex(), 'public_key': bytes(private.public_key).hex()},
        'scalarmult_base': {
            'private_key': bytes(private).hex(),
            'public_key': b.crypto_scalarmult_base(bytes(private)).hex(),
        },
        'sealed_box': {
            'recipient_seed': recipient_seed.hex(),
            'message': sealed_message.hex(),
            'ciphertext': SealedBox(recipient.public_key).encrypt(sealed_message).hex(),
        },
        'aead': {
            'key': aead_key.hex(), 'nonce': nonce.hex(), 'ad': ad.hex(), 'message': aead_message.hex(),
            'ciphertext': b.crypto_aead_xchacha20poly1305_ietf_encrypt(aead_message, ad, nonce, aead_key).hex(),
        },
        'secretstream': {'key': stream_key.hex(), 'ad': stream_ad.hex(), 'header': header.hex(), 'chunks': chunks},
        'argon2id': {
            'passphrase': passphrase.hex(), 'salt': salt.hex(), 'opslimit': opslimit, 'memlimit': memlimit,
            'key': nacl.pwhash.argon2id.kdf(32, passphrase, salt, opslimit=opslimit, memlimit=memlimit).hex(),
        },
        'blake2b': {
            'label': label.hex(), 'input': label_input.hex(),
            'digest': blake2b(label + label_input, digest_size=32, encoder=RawEncoder).hex(),
        },
        'blake2b_keyed': {
            'key': check_key.hex(), 'label': check_label.hex(),
            'digest': blake2b(check_label, digest_size=32, key=check_key, encoder=RawEncoder).hex(),
        },
        'pad': {
            'message': padded_message.hex(), 'block_size': 256,
            'padded': b.sodium_pad(padded_message, 256).hex(),
        },
    }


if __name__ == '__main__':
    target = HERE / 'pynacl.json'
    partial = target.with_suffix('.json.tmp')
    partial.write_text(json.dumps(make(), indent=2) + '\n')
    os.replace(partial, target)
