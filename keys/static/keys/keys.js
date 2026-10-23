// The archive's use of libsodium.js in lawyers' browsers (docs/m5-encryption-design.md §2-§5).
// A classic script, loaded after libsodium-sumo.js and libsodium-wrappers.js; it defines the
// global ArchiveKeys. Its counterparts on the server: keys.models (the safety number) and
// clips.upload_codes (the alphabet, and typing that forgives case, dashes and look-alikes).
(function () {
  'use strict';

  // Crockford's base32: digits and capital letters, without I, L, O and U.
  const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
  const PASSPHRASE_LENGTH = 20; // 100 bits
  const RECOVERY_KEY_LENGTH = 52; // 260 bits
  const GROUP_SIZE = 4;
  // Also in keys/static/keys/forget.js, which the login page loads without libsodium.js.
  const STORAGE = 'footage-archive-keys';

  const text = (value) => sodium.from_string(value);
  const b64 = {
    encode: (bytes) => sodium.to_base64(bytes, sodium.base64_variants.ORIGINAL),
    decode: (value) => sodium.from_base64(value, sodium.base64_variants.ORIGINAL),
  };

  function join(...parts) {
    const joined = new Uint8Array(parts.reduce((length, part) => length + part.length, 0));
    let offset = 0;
    for (const part of parts) {
      joined.set(part, offset);
      offset += part.length;
    }
    return joined;
  }

  function randomCode(length) {
    // 256 is a multiple of 32, so each random byte gives one character with no bias.
    return Array.from(sodium.randombytes_buf(length), (byte) => ALPHABET[byte % 32]).join('');
  }

  function display(code) {
    return code.match(new RegExp(`.{1,${GROUP_SIZE}}`, 'g')).join('-');
  }

  // The code as typed, normalised, or null if it cannot be one of `length` characters.
  function normalise(typed, length) {
    const code = typed.toUpperCase().replace(/[-\s]/g, '').replace(/O/g, '0').replace(/[IL]/g, '1');
    return code.length === length && [...code].every((char) => ALPHABET.includes(char)) ? code : null;
  }

  function passphraseKey(passphrase, salt, opslimit, memlimit) {
    return sodium.crypto_pwhash(32, text(passphrase), salt, opslimit, memlimit, sodium.crypto_pwhash_ALG_ARGON2ID13);
  }

  // A recovery key has 260 random bits: one hash turns it into a key, with no Argon2id.
  function recoveryWrappingKey(recoveryKey) {
    return sodium.crypto_generichash(32, join(text('fa-recoverykey-v1'), text(recoveryKey)));
  }

  const PRIVATE_KEY_LABEL = 'fa-personalkey-v1';

  function encryptPrivateKey(privateKey, wrappingKey) {
    const nonce = sodium.randombytes_buf(sodium.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES);
    const sealed = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(privateKey, text(PRIVATE_KEY_LABEL), null, nonce, wrappingKey);
    return join(nonce, sealed);
  }

  class WrongSecret extends Error {}
  class KeyMismatch extends Error {}

  // Throws WrongSecret if the passphrase or recovery key does not open it, and KeyMismatch if
  // what it opens does not give the public key the archive shows (design §3.4).
  function decryptPrivateKey(encrypted, wrappingKey, publicKey) {
    const split = sodium.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES;
    let privateKey;
    try {
      privateKey = sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
        null, encrypted.subarray(split), text(PRIVATE_KEY_LABEL), encrypted.subarray(0, split), wrappingKey,
      );
    } catch (error) {
      throw new WrongSecret();
    }
    if (!samePublicKey(privateKey, publicKey)) {
      throw new KeyMismatch();
    }
    return privateKey;
  }

  function samePublicKey(privateKey, publicKey) {
    const derived = sodium.crypto_scalarmult_base(privateKey);
    return derived.length === publicKey.length && sodium.memcmp(derived, publicKey);
  }

  // Six groups of five digits, as keys.models.safety_number computes them on the server.
  function safetyNumber(publicKey) {
    const digest = sodium.crypto_generichash(32, join(text('fa-safetynum-v1'), publicKey));
    const groups = [];
    for (let start = 0; start < 30; start += 5) {
      let value = 0;
      for (const byte of digest.subarray(start, start + 5)) {
        value = value * 256 + byte; // At most 40 bits: exact in a JavaScript number.
      }
      groups.push(String(value % 100000).padStart(5, '0'));
    }
    return groups.join(' ');
  }

  // Keys unlocked in this tab, kept for this login and this personal key only (design §4): a
  // page from another login, such as another lawyer's in the same tab, or showing a newer
  // key, discards them.
  function remember(loginId, keys) {
    sessionStorage.setItem(STORAGE, JSON.stringify({ login: loginId, personal: b64.encode(keys.personal) }));
  }

  function recall(loginId, publicKey) {
    try {
      const stored = JSON.parse(sessionStorage.getItem(STORAGE));
      if (stored && stored.login === loginId) {
        const personal = b64.decode(stored.personal);
        if (samePublicKey(personal, publicKey)) {
          return { personal };
        }
      }
    } catch (error) {
      // Nothing usable is stored.
    }
    forget();
    return null;
  }

  function forget() {
    sessionStorage.removeItem(STORAGE);
  }

  globalThis.ArchiveKeys = {
    PASSPHRASE_LENGTH, RECOVERY_KEY_LENGTH, WrongSecret, KeyMismatch, b64, randomCode, display, normalise,
    passphraseKey, recoveryWrappingKey, encryptPrivateKey, decryptPrivateKey, safetyNumber, remember, recall, forget,
  };
}());
