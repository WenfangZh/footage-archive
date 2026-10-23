// Checks that libsodium.js opens what PyNaCl made (tests/vectors/pynacl.json), and with
// --write also writes tests/vectors/libsodium_js.json, values made by libsodium.js for
// tests/test_libsodium_vectors.py to open with PyNaCl (README.md).
//   node tests/vectors/make_libsodium_js.mjs [--write]
// It loads the vendored files as classic scripts, as a lawyer's browser does. The archive
// itself never runs Node.

import { readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { runInThisContext } from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const vendor = join(here, '../../core/static/core/vendor/libsodium');
// In a browser, libsodium.js takes its random numbers from self.crypto, and it takes another
// path when it finds Node's process object, so that is hidden while it starts.
const nodeProcess = globalThis.process;
globalThis.self = globalThis;
globalThis.process = undefined;
try {
  for (const file of ['libsodium-sumo.js', 'libsodium-wrappers.js']) {
    runInThisContext(readFileSync(join(vendor, file), 'utf8'), { filename: file });
  }
  await globalThis.sodium.ready;
} finally {
  globalThis.process = nodeProcess;
}
const sodium = globalThis.sodium;

const hex = sodium.to_hex;
const unhex = sodium.from_hex;
const text = (value) => sodium.from_string(value);
const pattern = (length, start) => Uint8Array.from({ length }, (_, i) => (start + i) % 256);
const same = (a, b) => hex(a) === hex(b);

function check(v) {
  const failures = [];
  const expect = (name, ok) => { if (!ok) failures.push(name); };

  const seeded = sodium.crypto_box_seed_keypair(unhex(v.seed_keypair.seed));
  expect('seed_keypair', hex(seeded.publicKey) === v.seed_keypair.public_key);

  expect('scalarmult_base', hex(sodium.crypto_scalarmult_base(unhex(v.scalarmult_base.private_key))) === v.scalarmult_base.public_key);

  const recipient = sodium.crypto_box_seed_keypair(unhex(v.sealed_box.recipient_seed));
  const opened = sodium.crypto_box_seal_open(unhex(v.sealed_box.ciphertext), recipient.publicKey, recipient.privateKey);
  expect('sealed_box', hex(opened) === v.sealed_box.message);

  const a = v.aead;
  const decrypted = sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(null, unhex(a.ciphertext), unhex(a.ad), unhex(a.nonce), unhex(a.key));
  expect('aead', hex(decrypted) === a.message);

  const s = v.secretstream;
  const state = sodium.crypto_secretstream_xchacha20poly1305_init_pull(unhex(s.header), unhex(s.key));
  for (const chunk of s.chunks) {
    const pulled = sodium.crypto_secretstream_xchacha20poly1305_pull(state, unhex(chunk.ciphertext), unhex(s.ad));
    expect('secretstream', pulled && hex(pulled.message) === chunk.message && pulled.tag === chunk.tag);
  }

  const g = v.argon2id;
  const derived = sodium.crypto_pwhash(32, unhex(g.passphrase), unhex(g.salt), g.opslimit, g.memlimit, sodium.crypto_pwhash_ALG_ARGON2ID13);
  expect('argon2id', hex(derived) === g.key);

  const h = v.blake2b;
  const joined = new Uint8Array([...unhex(h.label), ...unhex(h.input)]);
  expect('blake2b', hex(sodium.crypto_generichash(32, joined)) === h.digest);

  const k = v.blake2b_keyed;
  expect('blake2b_keyed', hex(sodium.crypto_generichash(32, unhex(k.label), unhex(k.key))) === k.digest);

  expect('pad', hex(sodium.pad(unhex(v.pad.message), v.pad.block_size)) === v.pad.padded
    && same(sodium.unpad(unhex(v.pad.padded), v.pad.block_size), unhex(v.pad.message)));
  return [...new Set(failures)];
}

function make() {
  const seed = pattern(32, 11);
  const seeded = sodium.crypto_box_seed_keypair(seed);
  const recipientSeed = pattern(32, 12);
  const recipient = sodium.crypto_box_seed_keypair(recipientSeed);
  const sealedMessage = text('a data key, sealed to a lawyer');

  const aeadKey = pattern(32, 13), nonce = pattern(24, 14);
  const ad = text('case-id|case_label'), aeadMessage = text('a case label');

  const streamKey = pattern(32, 15), streamAd = text('clip-id|evidence_copy');
  const { state, header } = sodium.crypto_secretstream_xchacha20poly1305_init_push(streamKey);
  const chunks = [
    [text('first chunk'), sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE],
    [text('last chunk'), sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL],
  ].map(([message, tag]) => ({
    message: hex(message), tag,
    ciphertext: hex(sodium.crypto_secretstream_xchacha20poly1305_push(state, message, streamAd, tag)),
  }));

  const passphrase = text('ZYXW-VTSR-QPNM-KJHG-FEDC'), salt = pattern(16, 16);
  const opslimit = sodium.crypto_pwhash_OPSLIMIT_INTERACTIVE, memlimit = sodium.crypto_pwhash_MEMLIMIT_INTERACTIVE;

  const label = text('fa-safetynum-v1'), labelInput = pattern(32, 17);
  const checkKey = pattern(32, 18), checkLabel = text('fa-keycheck-v1');
  const paddedMessage = text('a context typed by a code holder');

  return {
    made_by: 'libsodium.js 0.8.4 (sumo)',
    seed_keypair: { seed: hex(seed), public_key: hex(seeded.publicKey) },
    scalarmult_base: { private_key: hex(seeded.privateKey), public_key: hex(sodium.crypto_scalarmult_base(seeded.privateKey)) },
    sealed_box: { recipient_seed: hex(recipientSeed), message: hex(sealedMessage), ciphertext: hex(sodium.crypto_box_seal(sealedMessage, recipient.publicKey)) },
    aead: {
      key: hex(aeadKey), nonce: hex(nonce), ad: hex(ad), message: hex(aeadMessage),
      ciphertext: hex(sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(aeadMessage, ad, null, nonce, aeadKey)),
    },
    secretstream: { key: hex(streamKey), ad: hex(streamAd), header: hex(header), chunks },
    argon2id: {
      passphrase: hex(passphrase), salt: hex(salt), opslimit, memlimit,
      key: hex(sodium.crypto_pwhash(32, passphrase, salt, opslimit, memlimit, sodium.crypto_pwhash_ALG_ARGON2ID13)),
    },
    blake2b: { label: hex(label), input: hex(labelInput), digest: hex(sodium.crypto_generichash(32, new Uint8Array([...label, ...labelInput]))) },
    blake2b_keyed: { key: hex(checkKey), label: hex(checkLabel), digest: hex(sodium.crypto_generichash(32, checkLabel, checkKey)) },
    pad: { message: hex(paddedMessage), block_size: 256, padded: hex(sodium.pad(paddedMessage, 256)) },
  };
}

const failures = check(JSON.parse(readFileSync(join(here, 'pynacl.json'), 'utf8')));
if (failures.length) {
  console.error(`libsodium.js could not open PyNaCl's values: ${failures.join(', ')}`);
  process.exit(1);
}
console.log("libsodium.js opens all of PyNaCl's values.");
if (process.argv.includes('--write')) {
  const made = make();
  const ownFailures = check(made);
  if (ownFailures.length) {
    console.error(`libsodium.js could not open its own values: ${ownFailures.join(', ')}`);
    process.exit(1);
  }
  const target = join(here, 'libsodium_js.json');
  writeFileSync(`${target}.tmp`, `${JSON.stringify(made, null, 2)}\n`);
  renameSync(`${target}.tmp`, target);
  console.log('Wrote libsodium_js.json.');
}
