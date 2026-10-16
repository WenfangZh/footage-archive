import hashlib
import hmac
import math
import re

import pytest

from clips import upload_codes

# A fixed, made-up code for tests: never issued to anyone.
CODE = '7K3QM9XA2B4CD6EF8GHJ0KMN1PQR'


def test_codes_carry_at_least_128_bits():
    assert upload_codes.LENGTH * math.log2(len(upload_codes.ALPHABET)) >= 128


def test_generated_codes_use_only_the_alphabet():
    code = upload_codes.generate()
    assert len(code) == upload_codes.LENGTH
    assert set(code) <= set(upload_codes.ALPHABET)


def test_generated_codes_differ():
    codes = {upload_codes.generate() for _ in range(1000)}
    assert len(codes) == 1000


def test_alphabet_has_no_lookalike_letters():
    assert len(upload_codes.ALPHABET) == 32
    assert not set('ILOU') & set(upload_codes.ALPHABET)


def test_display_groups_the_code_in_fours():
    assert upload_codes.display(CODE) == '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQR'


@pytest.mark.parametrize('typed', [
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQR',
    '7k3q-m9xa-2b4c-d6ef-8ghj-0kmn-1pqr',
    '7K3Q M9XA 2B4C D6EF 8GHJ 0KMN 1PQR',
    ' 7K3QM9XA2B4CD6EF8GHJ0KMN1PQR\n',
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-OKMN-IPQR',  # O typed for 0, I for 1
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-oKMN-lPQR',  # o typed for 0, l for 1
])
def test_normalise_accepts_how_people_type_codes(typed):
    assert upload_codes.normalise(typed) == CODE


@pytest.mark.parametrize('typed', [
    '',
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN',  # too short
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQRS',  # too long
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQU',  # U is not in the alphabet
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQ!',
    '7K3Q-M9XA-2B4C-D6EF-8GHJ-0KMN-1PQé',
])
def test_normalise_rejects_what_cannot_be_a_code(typed):
    assert upload_codes.normalise(typed) is None


def test_keyed_hash_is_hmac_sha256_with_the_upload_code_key(settings):
    expected = hmac.new(settings.UPLOAD_CODE_KEY.encode(), CODE.encode(), hashlib.sha256).hexdigest()
    assert upload_codes.keyed_hash(CODE) == expected
    assert re.fullmatch('[0-9a-f]{64}', expected)


def test_keyed_hash_depends_on_the_key(settings):
    first = upload_codes.keyed_hash(CODE)
    settings.UPLOAD_CODE_KEY = 'another-test-only-key-another-test-only-key'
    assert upload_codes.keyed_hash(CODE) != first


def test_keyed_hash_is_not_a_plain_hash():
    assert upload_codes.keyed_hash(CODE) != hashlib.sha256(CODE.encode()).hexdigest()


def test_the_same_code_typed_differently_has_the_same_hash():
    typed = '7k3q m9xa 2b4c d6ef 8ghj okmn ipqr'
    assert upload_codes.keyed_hash(upload_codes.normalise(typed)) == upload_codes.keyed_hash(CODE)
