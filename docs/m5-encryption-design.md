# M5: encrypting the vault (design)

Status: proposed, for review before any code is written (SPEC.md §10, §12 M5).
Decisions recorded in ADR 0007 (where keys live and who decrypts) and ADR 0008 (how vault files are encrypted).

This document answers what SPEC.md §10 asks of M5's design: key management (a newly admitted lawyer, revocation, recovery), how code holders read what is addressed to them without keys, which other data can be encrypted without breaking matching and alerts, and whether two-factor secrets should be. It also says how encrypted files still serve byte ranges, what stays readable on the server and why, and where the remaining weak points are.

## 1. What encryption protects against

Three kinds of attacker get different things from the server:

1. **At rest**: someone who holds a copy of the disk or the database, by seizure, compelled disclosure, a breach of storage or a leaked backup.
2. **Passive on the running server**: someone who can read its memory, files and logs while it runs, but does not change what it does.
3. **Active**: someone who changes the code the server runs, or the JavaScript it sends to browsers.

What M5 promises:

- Against (1), the vault holds nothing readable: no evidence copy, proxy, context, contact details, message or case label can be read without a lawyer's key or, for a code holder's own texts, the upload code.
- Against (2), only what passes through the server while it is watched: clips going through ingest, clips a lawyer opens, and texts a code holder or lawyer writes or a code holder reads. Never a private key, so nothing stored can be opened in bulk.
- Against (3), no promise. Ingest must read each original to hash, remux and make the proxy, so a server running malicious code can copy every new upload; and it can send lawyers JavaScript that copies their keys (§11).

So the target in SPEC.md §1 ("an operator who cannot read the footage at all") holds for footage the server stores, not for footage passing through it. This PR rewords §1 and §3 to say so.

Encryption protects against the server, not between lawyers. When ingest runs no case exists yet, so every lawyer's key can open every clip; case scoping stays the server's check, as today (§3.3).

## 2. Building blocks

Only libsodium, as SPEC.md §10 requires: PyNaCl 1.6.2 on the server, and libsodium.js 0.8.4 (the "sumo" build, the only one with Argon2id) in lawyers' browsers, copied into the repository like the map's libraries. Both are the same C library; sealed boxes, seeded key pairs, secretstream and Argon2id made by one open with the other (checked with test vectors during research).

| Purpose | libsodium function | PyNaCl | libsodium.js |
|---|---|---|---|
| Encrypt to a public key without a sender key (**sealed box**) | `crypto_box_seal` (X25519, XSalsa20-Poly1305) | `SealedBox` | `crypto_box_seal` |
| Encrypt a short text with a random key | `crypto_secretbox` (XSalsa20-Poly1305) | `SecretBox` | `crypto_secretbox_easy` |
| Encrypt a file in chunks | `crypto_secretstream_xchacha20poly1305` | `nacl.bindings` | same name |
| Key pair from a 32-byte seed | `crypto_box_seed_keypair` | `PrivateKey.from_seed` | `crypto_box_seed_keypair` |
| Key from a passphrase | `crypto_pwhash` (Argon2id) | not used | `crypto_pwhash` |
| Key from the upload code | `crypto_generichash` (BLAKE2b, with a personalisation label) | `nacl.hash.blake2b` | not used |
| Safety number | `crypto_generichash` over a public key | `nacl.hash.blake2b` | `crypto_generichash` |

No construction of our own: no chunk format, no key derivation scheme beyond one BLAKE2b call, no signatures.

Post-quantum: sealed boxes rest on X25519, which a large quantum computer could break, so ciphertext copied today could have its keys opened later. The contents themselves are encrypted with 256-bit symmetric keys, which such a computer does not break. libsodium 1.0.22 offers X-Wing (ML-KEM-768 with X25519), and libsodium.js 0.8.4 has it, but PyNaCl 1.6.2 does not, and ingest seals on the server. M5 stays with X25519; when PyNaCl offers X-Wing, a new group key of that kind and a rotation (§5.5) reseal every key without touching the files. Copies taken before that stay exposed to a future quantum computer (ADR 0007).

## 3. Keys

### 3.1 The keys and where they live

| Key | What it is | Held by | Stored on the server as |
|---|---|---|---|
| **Personal key** | One X25519 key pair per lawyer, made in their browser | the lawyer | public key; private key encrypted twice: under their key passphrase, and under their recovery key |
| **Key passphrase** | Chosen by the lawyer, separate from their login password | the lawyer, in memory | nothing (Argon2id salt and limits only) |
| **Recovery key** | 32 random bytes, shown once when the personal key is made | the lawyer, on paper with their 2FA recovery codes | nothing |
| **Group key** | One X25519 key pair shared by all lawyers; a new one after each revocation | every lawyer holding a key grant | public key; private key sealed to each lawyer's personal public key (one **key grant** each) |
| **Data key** | 32 random bytes per vault item (each file, each text) | nobody, at rest | sealed to the group key (and, for a code holder's texts, to the code holder key too) |
| **Code holder key** | One X25519 key pair per clip, derived from its upload code | whoever presents the code | public key only |
| **Server secrets** | The existing `SECRET_KEY` and upload-code HMAC key | the server | environment variables, unchanged |

The admin has no key. The admin cannot read the vault, now by construction rather than by rule (SPEC.md §5).

### 3.2 Data shape

Each sealed key records which public key it was sealed to, so a reader always knows which private key opens it, and a rotation can find what is left to reseal.

- `LawyerKey`: lawyer profile, public key, private key under the passphrase (with Argon2id salt and limits), private key under the recovery key, made at, retired at. At most one unretired key per lawyer (a partial unique constraint).
- `GroupKey`: public key, made at, retired at. At most one current (unretired, newest) key; during a rotation two are unretired (§5.5).
- `KeyGrant`: group key, lawyer key, the group private key sealed to the lawyer key, granted by (lawyer profile), granted at. One per group key and lawyer key.
- `VaultFile` gains: group key, sealed data key, plaintext size. The file on disk is the secretstream (§6).
- `VaultText`: its plaintext `content` becomes the secretbox ciphertext, plus the group key and the data key sealed to it, and the data key sealed to the code holder key.
- `Clip` gains the code holder public key.
- `Case.label` becomes the label sealed to the lawyer's personal public key, plus which lawyer key.
- `LawyerProfile` gains whether the admin confirmed the safety number at admission (§5.2), and the confirmed safety number.

During the move from plaintext (§10), the new fields allow empty values; a later migration makes them required once no plaintext row is left, and fails loudly if one is.

### 3.3 What a lawyer needs to read a clip

Three things together: the ciphertext, the data key sealed to the group key, and a private key that opens it (their personal key opens their key grant, which holds the group private key). The server hands the first two only to an admitted, unsuspended lawyer whose open case matches the clip, as today. A key on its own reads nothing: someone holding a lawyer's key also needs the encrypted data, which only the server serves, or a breach provides.

### 3.4 Safety number

A short, readable code computed from a personal public key (BLAKE2b of the key, shown as 12 groups of 5 digits, as Signal does), so that two people can check by phone or in person that the key the server shows is the key the lawyer made. Each lawyer sees their own on their key page.

## 4. Unlocking in the browser

A lawyer logs in as today (password and second factor). Pages that show vault content load libsodium.js and a small script of our own. The first such page in a browser tab asks for the key passphrase in a field that is never submitted: the script fetches the lawyer's encrypted private key and their key grant, derives the passphrase key with Argon2id (libsodium's "moderate" limits, 256 MiB and 3 passes, so about a second), opens both, and keeps the two private keys in the tab's `sessionStorage`, so the lawyer is not asked again on every page. Logging out clears it; closing the tab clears it.

Trade-off: any script running in the page can read `sessionStorage`, so a cross-site scripting bug would expose the keys. Every lawyer page gets the strict Content Security Policy the map page already has (only the archive's own scripts, no inline script), extended with `'wasm-unsafe-eval'`, which libsodium.js needs to run compiled code. Asking for the passphrase on every page would avoid storing the keys, but in a site of separate pages it would mean typing it many times an hour.

Without JavaScript, or before unlocking, a lawyer can still log in, create cases and search; clip pages show the clip's index data (times, places, tags, edits) and say that the vault content needs the key.

## 5. Key management

### 5.1 Setting up a personal key

An applicant sets up their personal key after their password and second factor, before admission, so that the admin can check it. The browser makes the key pair, asks for a key passphrase (twice; at least 12 characters, and the page says it is not the login password and is not recoverable by anyone), makes the recovery key, shows it once with a "save this now" message, as for 2FA recovery codes, and asks the applicant to type its last group back. It then uploads the public key and the private key encrypted under each. The server never sees the passphrase, the recovery key or the private key.

The recovery key is shown in Crockford base32 in groups, like an upload code, but under its own name (GLOSSARY.md: recovery key, not recovery code).

### 5.2 Admission: the admin checks the key

To the admission steps of SPEC.md §5, M5 adds one: the admin compares the applicant's safety number with them, through the bar directory's contact details (the channel already used to verify registration). The admission form records the safety number the admin confirmed; the server refuses to admit without it, and refuses if the applicant's current key no longer has that number.

### 5.3 Key grant: a lawyer hands over the group key

An admitted lawyer without a key grant can log in, create cases and search, but cannot read the vault. Lawyers holding a grant see a list of admitted colleagues waiting for one, with the vouching lawyer's name when the vouch came from a lawyer (SPEC.md §5).

To grant, a lawyer compares the newcomer's safety number with them directly (by phone or in person; the page says not by email or message through the archive) and confirms. Their browser opens the group private key with their own personal key, seals it to the newcomer's public key, and uploads the grant. The server accepts it only if the newcomer is admitted and unsuspended, the admin's confirmed safety number matches the newcomer's current key, and the granting lawyer holds a grant on that group key. Each grant writes an audit entry naming both lawyers.

So a grant needs both the admin and a lawyer, each comparing the safety number through their own channel. The admin's part is enforced by the server; the lawyer's part is cryptographic, since only a lawyer's browser can seal the group key. Against someone who can write to the database, the lawyer's own comparison catches a swapped key. A signing key for the admin, so that lawyers' browsers could check the admin's approval themselves, is left for later (§11).

**The first lawyer.** While no group key exists, the first lawyer to unlock after admission makes it in their browser and grants it to themselves; the server accepts a new group key only when none exists (or during rotation, §5.5). Until a group key exists, the upload page is closed (§10), so nothing is ever stored unencrypted for want of a key.

### 5.4 Suspension and reinstatement

Nothing changes in the keys. A suspended lawyer is refused every lawyer page and request, as today, including the encrypted data, the sealed keys and a request carrying a data key (§6.3); with their key but without the data, they read nothing. Reinstatement restores access with the same keys. Rotating at suspension would make reinstatement a full new grant, while SPEC.md §5 has suspension remove access "and nothing else"; an admin who fears for the keys revokes instead.

### 5.5 Revocation and rotation

Revocation deletes the lawyer's key grants and retires their personal key, then asks for a rotation: a revoked lawyer may have copied the group key out of their browser, and with it could open any data key they later obtain through a breach.

The server cannot rotate, since it has no key; the next lawyer to unlock does, in their browser:

1. It makes a new group key pair and seals it to every lawyer key still holding a grant on the current group key (suspended lawyers included, so that reinstatement needs nothing new), and uploads the new key and grants in one request. From then on, ingest seals to the new key.
2. It reseals in batches: the server sends data keys still sealed to the old group key; the browser opens each with the old private key and seals it to the new public key; the server replaces each sealed key only if it is still sealed to the old one, so two browsers resealing at once cannot undo each other.
3. When none is left, the server retires the old group key and deletes its grants.

It can be interrupted at any point and resumes at the next unlock. Until it ends, items are sealed to one key or the other, and readers use whichever their row names. Two lawyers unlocking at once cannot both start a rotation: the server accepts one new group key and the other browser joins the resealing. The start and end of a rotation each write an audit entry naming the lawyer whose browser did it.

Limits:

- The files are not re-encrypted (2 GB each, and the server would need each data key from a browser); only the sealing of their keys changes. The revoked lawyer still knows the data keys of clips they opened, and may hold those clips anyway.
- Backups made before the rotation still hold the old sealed keys. Keeping backups only for a limited time belongs to deployment.
- Until a lawyer unlocks after a revocation, new clips are still sealed to the old key.
- The rotating browser opens every data key, not only its lawyer's cases. That gives it nothing it could not do already: any group key holder can (§1).
- The list of lawyer keys to seal the new group key to comes from the server. Someone who can write to the database at that moment could add a key of their own; a signing key for the admin would close this (§11).

### 5.6 Recovery

- **Forgotten key passphrase, recovery key at hand**: the browser opens the second copy of the private key with the recovery key and asks for a new passphrase. The key, its safety number and its grants stay the same. The recovery key stays valid (it is not shown again); a lawyer who thinks it was seen can make a new personal key instead (below).
- **Both lost**: like an admission. The admin confirms the person through the bar directory's contact details and resets their key, which retires it and deletes their grants (an audit entry). The lawyer sets up a new personal key (§5.1), the admin confirms its safety number (§5.2), and a lawyer grants the group key again (§5.3). What was sealed only to the old personal key is lost: case labels show as unreadable (and from M6, their messages).
- **Lost password or second factor**: SPEC.md §13 still leaves this open, and M5 does not add it. M5 changes what is at stake: whoever got into a lawyer's account, the admin included, would still lack the key passphrase, so would see no footage, context, contact details or case labels, only the case areas and time windows and audit entries, which the admin can already see.
- **Every key lost**: if every lawyer lost both passphrase and recovery key, everything in the vault would be unreadable for good. Asking each lawyer to type back part of their recovery key at setup is the guard against this.

## 6. Files: evidence copies and proxies

### 6.1 Encrypting at ingest

Ingest makes the evidence copy and proxy as today, then stores each through `vault.store_file`, which now draws a random data key, encrypts the file with libsodium's secretstream in 64 KiB chunks (marking the last chunk as final, so a cut-off file is detected), seals the data key to the current group public key, and returns what `record_file` stores: the file's name, the sealed data key, which group key, and the plaintext size. Ingest's temporary plaintext files are deleted as today (§9).

### 6.2 Byte ranges and size

Secretstream decrypts in order: it cannot start at chunk N. Each request for a byte range therefore decrypts from the start of the file and discards what comes before the range. Measured on the development server (2 vCPU) during research: 761 MB/s, so about 0.07 s to reach the end of a 50 MB proxy, and 2.6 s to resume a 2 GB evidence copy near its end. That is acceptable for previews and for the rare resumed download. A format that can seek would mean independent encrypted chunks, which is a construction of our own (no maintained Python library offers it), so it is ruled out by the hard rule against custom cryptography (ADR 0008).

`vault.open_file(clip, kind, data_key)` returns a read-only file object that decrypts as it is read, whose `seek` restarts decryption and skips to the position, and which carries the plaintext `size` from the `VaultFile` row. `open_or_404` takes that size instead of `os.fstat(fileno())`; `file_response` is unchanged. A file whose authentication fails partway (tampered, or cut off) raises an error that ends the response, and is logged by its random name only.

### 6.3 Handing the data key to the server

A `<video>` element and a download link cannot add headers to their requests, and a key in a URL would end up in web server logs. So the clip page's script opens the data keys of the clip's proxy and evidence copy and posts them to a new view on the clip (case and match checked as for the preview). The server checks each key by decrypting the file's first chunk, stores nothing, and sets a cookie per file:

- `HttpOnly` (page scripts cannot read it back), `Secure`, `SameSite=Strict`;
- `Path=/lawyer/cases/<case>/clips/<clip>/`, so it is sent only with that clip's preview and download in that case;
- expiring after 2 hours, like the preview and download logging windows.

The preview and download views read it, and refuse with 403 when it is missing or wrong. A resumed download after the cookie expired needs the clip page opened again. Research confirmed Chrome sends such a cookie with a `<video>` range request; Firefox, Safari and resumed downloads are tested in the first implementation step, and if one fails, the fallback is a short-lived server-side record keyed by a random id in the cookie (the data key held in the cache, never the database).

Django's error reports would show cookies; nothing sends error reports today, and SPEC.md §13's open question about them now covers the data key cookies too.

## 7. Texts

### 7.1 Context and contact details

Each text gets its own data key and is encrypted with secretbox; the data key is sealed twice, to the group key (for lawyers) and to the clip's code holder key (for the code holder). Editing writes a new data key and ciphertext over the old row, so old values are still never kept (SPEC.md §5).

Lawyers: the clip page carries the ciphertext and the sealed data key; the script opens and shows them. Contact details and context never reach the server in plaintext after they are written.

### 7.2 Code holders

The upload code (140 random bits) is already strong enough to be key material. When the uploader confirms the form, the server derives a seed from the code with BLAKE2b under a fixed label of its own (`footage-archive code holder key`), makes the key pair from it, and stores only the public key on the clip. The upload code's lookup hash (HMAC-SHA-256 with the server secret) is unrelated to this seed, so the stored hash does not reveal the key.

A code holder's every visit already sends the code in a form submission. During that request the server derives the private key, opens the code holder's copies of the data keys and shows the texts; it keeps nothing. Without the code, nothing addressed to the code holder can be opened, by the server or anyone else. Nothing changes for code holders, and the code still never travels in a URL, cookie or session (SPEC.md §5).

### 7.3 Messages (M6)

M6 builds messages on the same pattern: a message from a lawyer has its data key sealed to the code holder key and to that lawyer's personal key; a reply, to the code holder key and the personal key of the lawyer addressed. Other lawyers cannot read them. A lawyer who loses both passphrase and recovery key loses their messages, like their case labels.

### 7.4 Case labels

When a lawyer creates a case, the server seals its label to their personal public key and stores only that; it sees the label once, in the form submission. Pages listing cases show the label through the script. The admin never sees case labels, as today. A lawyer whose personal key was reset sees "label unreadable" on older cases.

## 8. What stays readable on the server, and why

| Data | Readable? | Why |
|---|---|---|
| The index (times, cells, tags, edits, hash record, status) | Yes | Matching and the code holder's form need it; it is limited to what is harmless if exposed (SPEC.md §6). |
| Case time window and area | Yes | Matching runs on the server at every search, clip page, preview and download; the admin reads them to approve a larger case. Encrypting them would also mean encrypting the audit log's clip ids, which together with the index reveal roughly the same thing. |
| Audit log | Yes | Alerts count its entries; the admin reads all of it; views and previews are deduplicated and closing by disuse read from it; the tag history is drawn from it. |
| Lawyer's reason | Yes | The admin must read it; it is deleted once the admin decides. |
| Two-factor secrets | Yes | See below. |
| Public keys, sealed keys, safety numbers confirmed | Yes | Not secret. |

**Two-factor secrets.** django-otp has no supported way to encrypt authenticator secrets or hash recovery codes. A recovery code logs in as well as the authenticator secret, so encrypting one without the other gains nothing; hashing recovery codes means changing how django-otp checks them, which is close to writing our own authentication. With M5, getting into a lawyer's account no longer gives the vault, so what someone with a copy of the database and a cracked password gains is the index, cases and audit entries added after the copy, until the password changes; as the admin, they could also suspend, revoke or admit, though an admitted impostor still needs a lawyer's key grant. The better defence is encrypted backups at deployment (SPEC.md §12 Later). This answers SPEC.md §10's question: not in M5.

The residual risk in SPEC.md §3 stays, reworded: a seized server shows which lawyer looked into which incident and which clips they opened, but not what any clip shows.

## 9. Quarantine and ingest: the weakest link

Quarantine is the one place the server must hold readable footage, and it holds the most sensitive version of it: the original, with its precise GPS, device make and model and creation software, which identify the filmer and, for a clip filmed from a window, their home (ADR 0002).

**What lies there, and for how long.**

- From upload until the uploader confirms the form: at most 2 hours (configurable), after which an unconfirmed original is deleted (SPEC.md §8).
- From confirmation until ingest ends: at most about 1.5 hours with all retries (SPEC.md §9), then the original is deleted.
- While ingest runs: also the evidence copy and proxy as temporary plaintext files, until they are encrypted into the vault and deleted.
- In the database during that time: the clip's index data, and the GPS position snapped to its cell once ingest reads it. The precise position is never written to the database (ADR 0002), but it is in the original file until the file is deleted.

**Who can reach it.** Nothing in quarantine is served over the web (SPEC.md §8, §9). The folder is private to the archive's user on the server. ffprobe and ffmpeg read the original, so a flaw in them is the most likely way for a malicious upload to run code on the server; the archive accepts only the container formats phones record in, limiting which parts of ffmpeg ever run, but ffmpeg from Ubuntu's `universe` gets security fixes only through Ubuntu Pro (an open follow-up). Such code would run as the archive's user, could read quarantine and, while it ran, everything else the server reads (attacker type 3).

**What each attacker gets.**

- At rest, at a random moment: the originals uploaded in the last few hours, with their GPS. Everything older is encrypted.
- Passive on the running server, for a period: every original uploaded during that period.
- Active: the same, plus everything §1 says.

**What could make it smaller** (none is in M5; each is a deployment or later decision):

- A RAM-backed quarantine and temporary folder (tmpfs): nothing readable survives a power-off or a disk seizure. Up to 2 GB per upload in RAM limits how many uploads can wait at once; swap must be encrypted or off.
- Full-disk encryption on the server: protects a powered-off or removed disk, not a running server.
- A shorter confirmation window than 2 hours.
- Ingest on a separate machine with no web access, fed only through the queue: a flaw reached through the web front end would no longer reach quarantine, and a flaw in ffmpeg would no longer reach the web front end or the database.
- Encrypting originals to the group key at upload, with ingest asking a lawyer's browser for the key: the server could no longer process uploads on its own, and uploads would wait for a lawyer to log in. Rejected for now.

## 10. Moving existing data, and closing uploads without a group key

- **Uploads before a group key exists**: the upload page says uploads are not open yet, and the server refuses uploads, so ingest never has to store a file it cannot encrypt. In a deployment, the first lawyer is set up before uploads open.
- **Plaintext already in the vault** (development data only): a management command, `encrypt_vault`, encrypts each plaintext file and text to the current group key, writing each file to a new name and switching the row over in one step, so a crash leaves either the old or the new state, never a half-written file in use; run again, it skips what is already encrypted. Code holder copies of existing texts cannot be made, since the server does not know the codes: the code holder's form shows them empty until the code holder enters them again, and the command says how many it left without one.

## 11. Known limits

- **JavaScript served by the archive** (attacker type 3): the server that stores the ciphertext also sends the code that holds the keys, so a compromised server can steal lawyers' keys. Every encryption in a web page has this limit; browser extensions that check the served code (WEBCAT, Code Verify) are not usable for an ordinary site yet. A separate application holding the keys, as SecureDrop does, is the answer if this ever becomes the main threat.
- **Ingest sees every upload** in plaintext (§9).
- **No case scoping by encryption** (§1).
- **Grants and rotations trust the server's list of lawyer keys** beyond the safety number checks (§5.3, §5.5). Next step: a signing key for the admin, who signs each confirmed key, with lawyers' browsers refusing to seal the group key to an unsigned one. It needs the admin to hold a key, with its own recovery, and lawyers' browsers to know the admin's public key from a source the server cannot swap.
- **Post-quantum**: X25519 until PyNaCl offers X-Wing (§2).
- **Backups made before a rotation** still open with the old group key (§5.5).
- **Clips already downloaded** stay with the lawyer, whatever happens to their access.
- **Keys in `sessionStorage`** are exposed to a cross-site scripting bug (§4).

## 12. What M5 is done when

Proposed for SPEC.md §12 (this PR adds it):

- tests show that after upload and ingest, the vault's files and the database hold no plaintext of the evidence copy, proxy, context, contact details or case label;
- that a preview and a download with the right data key serve the right bytes, byte ranges included (a range near the end, a suffix range, an unsatisfiable range), and that without it, or with a wrong one, they serve nothing;
- that a code holder reads their own context and contact details with the code, and nothing can be opened without it;
- that the server refuses to admit an applicant without a confirmed safety number, refuses a key grant from someone without one, to someone not admitted or suspended, or to a key other than the confirmed one, and that grants, key resets and rotations each write an audit entry;
- that revocation deletes the lawyer's grants and starts a rotation, that after a rotation no key is sealed to the old group key, and that ingest seals to the new key once a rotation has started;
- that a suspended lawyer can neither hand over a data key nor fetch encrypted data;
- that uploads are refused while no group key exists;
- that values made by libsodium.js (sealed boxes, seeded key pairs, secretstream, Argon2id), stored in the repository as test vectors, open with PyNaCl, and the reverse;
- and, checked by hand in Firefox and Chrome, that a lawyer can set up a key, unlock, read a clip's texts, watch its proxy and download its evidence copy, whose SHA-256 matches its hash record.

## 13. Dependencies and changes outside the vault

- **PyNaCl 1.6.2** (Apache-2.0) in `requirements.txt`.
- **libsodium.js 0.8.4, sumo build** (ISC), two files copied into the repository with their licence and checksums, as for the map's libraries.
- **Content Security Policy** on all lawyer and key pages: the map page's policy, plus `script-src 'self' 'wasm-unsafe-eval'`.
- **Audit log**: new actions for key grants, key resets, and rotation start and end.
- **Accounts**: key setup for applicants, the safety number at admission, the admin's key reset, the waiting-for-a-key list.

## 14. Build order (to be proposed in detail once this is approved)

Roughly: (1) the dependencies, test vectors and a check of the data key cookie in Firefox and Safari; (2) encrypted files and texts in the vault behind its existing functions, with the group key and data keys, and the `encrypt_vault` command; (3) personal keys, unlocking and key grants, with the admission change; (4) code holder keys; (5) case labels; (6) revocation and rotation, and recovery. Nearly every step changes encryption, accounts or the audit log, so nearly every PR is reserved to the user, each with an Opus review.
