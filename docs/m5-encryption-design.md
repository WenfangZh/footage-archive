# M5: encrypting the vault (design)

Status: proposed, for review before any code is written (SPEC.md §10, §12 M5).
Decisions recorded in ADR 0007 (where keys live and who decrypts) and ADR 0008 (how vault files are encrypted).

This document answers what SPEC.md §10 asks of M5's design: key management (a newly admitted lawyer, revocation, recovery), how code holders read what is addressed to them without keys, which other data can be encrypted without breaking matching and alerts, and whether two-factor secrets should be. It also says how encrypted files still serve byte ranges, what stays readable on the server and why, and where the remaining weak points are.

## 1. What encryption protects against

Four kinds of attacker get different things from the server:

1. **At rest**: someone who holds a copy of the disk or the database, by seizure, compelled disclosure, a breach of storage or a leaked backup.
2. **Passive on the running server**: someone who can read its memory, files and logs while it runs, but does not change what it does.
3. **Database writer**: someone who can change rows in the database (a flaw that lets them write SQL, a tampered backup restored), but not the code the server runs.
4. **Active**: someone who changes the code the server runs, or the JavaScript it sends to browsers.

What M5 promises:

- Against (1), the vault holds nothing readable without a key: no evidence copy, proxy, context, contact details, message or case label opens without a lawyer's key or, for a code holder's own texts, the upload code. Each lawyer's private key is stored too, encrypted under their key passphrase, so the protection rests on those passphrases resisting offline guessing; that is why the passphrase is generated, never chosen (§5.1). Files deleted from a disk are not always erased from it (§9).
- Against (2), only what passes through the server while it is watched: clips going through ingest, clips a lawyer opens, and texts a code holder or lawyer writes or a code holder reads. Never a private key, so nothing stored can be opened in bulk.
- Against (3), the safety number checks stop a swapped key at a key grant (§5.3), but not everything: a database writer could replace the group public key with one of their own, so that new uploads are sealed to them (§11). They cannot read what is already stored.
- Against (4), no promise. Ingest must read each original to hash, remux and make the proxy, so a server running malicious code can copy every new upload; and it can send lawyers JavaScript that copies their keys (§11).

So the target in SPEC.md §1 ("an operator who cannot read the footage at all") holds for footage the server stores, not for footage passing through it. This PR rewords §1 and §3 to say so.

Encryption keeps contents secret from the server; it does not stop the server from writing contents of its own. Ingest encrypts to a public key, so anyone holding that public key, the server included, can encrypt a forged context or file. That the evidence copy is the frames that were uploaded is shown, as before, by the hash record (SPEC.md §9), which a code holder can check against the original on their phone.

Encryption protects against the server, not between lawyers. When ingest runs no case exists yet, so every lawyer's key can open every clip; case scoping stays the server's check, as today (§3.3).

## 2. Building blocks

Only libsodium, as SPEC.md §10 requires: PyNaCl 1.6.2 on the server, and libsodium.js 0.8.4 (the "sumo" build, the only one with Argon2id) in lawyers' browsers, copied into the repository like the map's libraries. Both are the same C library; sealed boxes, seeded key pairs, secretstream and Argon2id made by one open with the other (checked with test vectors during research).

| Purpose | libsodium function | PyNaCl | libsodium.js |
|---|---|---|---|
| Encrypt to a public key without a sender key (**sealed box**) | `crypto_box_seal` (X25519, XSalsa20-Poly1305) | `SealedBox` | `crypto_box_seal` |
| Encrypt a short text with a random key, bound to what it belongs to | `crypto_aead_xchacha20poly1305_ietf` | `nacl.bindings` | same name |
| Encrypt a file in chunks, bound to what it belongs to | `crypto_secretstream_xchacha20poly1305` | `nacl.bindings` | same name |
| Hide a text's length | `sodium_pad` | `nacl.bindings` | `pad` |
| Key pair from a 32-byte seed | `crypto_box_seed_keypair` | `PrivateKey.from_seed` | `crypto_box_seed_keypair` |
| Public key from a private key | `crypto_scalarmult_base` | `nacl.bindings` | same name |
| Key from a passphrase | `crypto_pwhash` (Argon2id) | not used | `crypto_pwhash` |
| Upload code to seed, safety number, key check value | `crypto_generichash` (BLAKE2b-256; keyed for the key check value) | `nacl.hash.blake2b` | `crypto_generichash` |

Every encryption of an item carries, as additional data that the decryption checks, what the item is: the clip's id and the kind of item (`evidence_copy`, `proxy`, `context`, `contact_details`), or the case's id for a case label. A ciphertext moved to another clip or kind, or a sealed key moved with it, fails to open.

The BLAKE2b uses each start their message with a label of their own, so that no value can stand in for another: `fa-codeholder-v1` (code holder seed), `fa-safetynum-v1` (safety number), `fa-keycheck-v1` (key check value), `fa-recoverykey-v1` (the key a recovery key stands for: its 260 random bits need no Argon2id). The encrypted copies of a private key carry the label `fa-personalkey-v1` as additional data; they need not name their public key as well, since unlocking checks that the private key gives the public key shown (§3.4), which catches a swapped key or copy, and says so, where a failed decryption could only say the passphrase was wrong. Plain `crypto_generichash` works the same in PyNaCl and libsodium.js, and the test vectors cover it labelled and keyed; the code holder seed is computed only on the server.

No construction of our own: no chunk format, no key derivation scheme beyond single BLAKE2b calls, no signatures. The key check value (§5.5) is the one use of BLAKE2b as a keyed hash over a fixed label; the review should look at it in particular.

Post-quantum: sealed boxes rest on X25519, which a large quantum computer could break, so ciphertext copied today could have its keys opened later. The contents themselves are encrypted with 256-bit symmetric keys, which such a computer does not break. libsodium 1.0.22 offers X-Wing (ML-KEM-768 with X25519), and libsodium.js 0.8.4 has it, but PyNaCl 1.6.2 does not, and ingest seals on the server. M5 stays with X25519; when PyNaCl offers X-Wing, a new group key of that kind and a rotation (§5.5) reseal every key without touching the files. Copies taken before that stay exposed to a future quantum computer (ADR 0007).

## 3. Keys

### 3.1 The keys and where they live

| Key | What it is | Held by | Stored on the server as |
|---|---|---|---|
| **Personal key** | One X25519 key pair per lawyer, made in their browser | the lawyer | public key; private key encrypted twice: under their key passphrase, and under their recovery key |
| **Key passphrase** | 100 random bits, generated in the browser, typed to unlock the personal key | the lawyer: in memory, on paper or in a password manager | nothing (Argon2id salt and limits only) |
| **Recovery key** | 260 random bits (52 characters of Crockford base32), generated in the browser, shown once | the lawyer, on paper with their 2FA recovery codes | nothing |
| **Group key** | One X25519 key pair shared by all lawyers; a new one after each revocation | every lawyer holding a key grant | public key; private key sealed to each lawyer's personal public key (one **key grant** each) |
| **Data key** | 32 random bytes per vault item (each file, each text, each case label) | nobody, at rest | sealed to the group key (a case label's, to its lawyer's personal key; a code holder's texts', to the code holder key as well); plus its key check value |
| **Code holder key** | One X25519 key pair per clip, derived from its upload code | whoever presents the code | public key only |
| **Server secrets** | The existing `SECRET_KEY` and upload-code HMAC key | the server | environment variables, unchanged |

The admin has no key. The admin cannot read the vault, now by construction rather than by rule (SPEC.md §5).

### 3.2 Data shape

Each sealed key records which public key it was sealed to, so a reader always knows which private key opens it, and a rotation can find what is left to reseal.

- `LawyerKey`: lawyer profile, public key, private key under the passphrase (with Argon2id salt and limits), private key under the recovery key, made at, admin confirmed at, last unlocked on (a day, for the draws of §5.5), retired at. At most one unretired key per lawyer (a partial unique constraint). Retiring a key erases both encrypted copies of its private key; the public key stays, for the audit log's sake. "Admin confirmed at" belongs to the key, so a new key starts unconfirmed by construction.
- `Rotation`: the group key being replaced, requested at, ended at. At most one not ended. Each revocation or exposure report that requested or restarted it is in the audit log.
- `RotationAttempt`: one try at a rotation (§5.5): the rotation, the rotator (a lawyer profile), drawn at, ended at, and its outcome (`ended`, `rejected`, or `abandoned` with why), set once. At most one not ended per rotation.
- `GroupKey`: public key, made at, the attempt that made it (empty for the first key and after starting over), and a status: `current`, `rotation_requested` (current, and a rotation is due), `checking` (the new key of a rotation, waiting for its checks), `retiring` (the old key during a rotation), `retired`, `rejected` (a new key whose checks failed), `abandoned` (a new key whose rotation was given up). Rows are never deleted. At most one key is `current`, `rotation_requested` or `retiring`; one key is `checking` exactly while one is `retiring`. The database enforces the first with a partial unique constraint; the second is kept by the few functions that change a status, all under the group key lock.
- `KeyGrant`: group key, lawyer key, the group private key sealed to the lawyer key, granted by (lawyer profile), granted at, checked at (when the recipient's own browser confirmed it, §4). One per group key and lawyer key.
- `RotationCheck`: one check of an attempt (§5.5): the rotation attempt, its slot (1, 2, or confirming), the lawyer profile drawn for it, drawn at, its result (none yet, passed, or failed with the failing items; set once) and replaced at (when the slot was drawn again; a result from a replaced holder is refused). Partial unique constraints allow, per attempt, one unreplaced check per slot and one per lawyer, so one lawyer never holds two. Eligibility always compares lawyer profiles, never lawyer keys.
- `SealedKey`: the item it belongs to (a vault file or a vault text), which public key it is sealed to (a group key, or the code holder key for a text), the sealed data key, and who sealed it (a rotator's lawyer profile, or empty for the server). At most one per item and public key: during a rotation an item has one per group key, and a code holder's texts one more for the code holder.
- `VaultFile` gains the data key's key check value. Its plaintext size is not stored: it follows from the stored file's size, since every chunk but the last is 64 KiB and each adds a fixed overhead (ADR 0008).
- `VaultText`: its plaintext `content` becomes the padded, encrypted text, plus its key check value.
- `Case.label` becomes the encrypted, padded label; its data key is sealed to the lawyer's personal key. A case has one label sealed to one key, so the case itself holds the sealed key, the key it is sealed to, and the key check value, rather than a `SealedKey`.
- `Clip` gains the code holder public key.

During the move from plaintext (§10), the new fields allow empty values; a later migration makes them required once no plaintext row is left, and fails loudly if one is. A case label is the exception: its encryption is bound to the case's id, which exists only once the row is inserted, so a case is inserted without a label and its label sealed in the same transaction. Its fields therefore stay optional for good; a case without a label holds no plaintext and shows its number instead.

### 3.3 What a lawyer needs to read a clip

Three things together: the ciphertext, the data key sealed to the group key, and a private key that opens it (their personal key opens their key grant, which holds the group private key). The server hands the first two only to an admitted, unsuspended lawyer whose current personal key holds a grant on a group key in use (`current`, `rotation_requested`, `checking` or `retiring`), and whose open case matches the clip, as today; the gate covers every view that hands over keys or encrypted data, the preview and download reached with a data key cookie (§6.3) and the rotation and check views included; a lawyer whose key was just replaced gets nothing from the vault until the new key is granted, even if an old key is still in their browser. A key on its own reads nothing: someone holding a lawyer's key also needs the encrypted data, which only the server serves, or a breach provides.

### 3.4 Safety number

A code computed from a personal public key, which two people read to each other to check that the key the server shows is the key the lawyer made: BLAKE2b-256 of the label `fa-safetynum-v1` followed by the public key, of which the first 30 bytes are read as six 5-byte big-endian numbers, each taken modulo 100 000 and written as 5 digits. Thirty digits in six groups, about 100 bits, as Signal does for one person's half of its safety number.

A lawyer's own safety number is computed in their browser from the public key it derives from their decrypted private key, never from the public key the server stores; if the two differ, the page refuses to go on and says why. When granting, the number shown is computed from exactly the public key the grant will be sealed to.

## 4. Unlocking in the browser

A lawyer logs in as today (password and second factor). Pages that show vault content load libsodium.js and a small script of our own. The first such page in a browser tab asks for the key passphrase in a field that is never submitted: the script fetches the lawyer's encrypted private key and their key grants, derives the passphrase key with Argon2id (libsodium's "interactive" limits, 64 MiB and 2 passes: with a 100-bit generated passphrase, slowness adds little, and phones have little memory), opens the private key, checks that it gives the stored public key, opens each grant and checks that the group private key gives that group key's public key. The first time a grant opens correctly, the browser reports it, which sets the grant's "checked at"; a grant that fails is reported too, raising an alert to the admin and a request for a new grant; the server deletes it then, unless it is the last grant held on its group key by a lawyer who is not revoked, which it keeps, since it cannot tell a true report from a wrong one and deleting it would make the archive start over (§5.7).

The script keeps the personal private key in the tab's `sessionStorage`, so that the lawyer is not asked again on every page, together with the id of the login it was unlocked in (given in each page); each page opens the grants again with it, so that one made or replaced since is never missed. A page whose login id differs (another lawyer in the same tab, or a new login after the old one expired) discards it. Logging out sends `Clear-Site-Data: "storage"`, which empties the browser's storage for the archive's site.

`sessionStorage` is not only in memory: browsers save it to disk to restore tabs after a restart, so the keys reach the lawyer's own disk, as the clips they download do. The lawyer's device is part of what they must protect anyway. Any script running in the page can also read `sessionStorage`, so a cross-site scripting bug would expose the keys: every lawyer page gets the strict Content Security Policy the map page already has (only the archive's own scripts, no inline script), extended with `'wasm-unsafe-eval'`, which libsodium.js needs to run compiled code. Asking for the passphrase on every page would avoid storing the keys, but in a site of separate pages it would mean typing it many times an hour.

Without JavaScript, or before unlocking, a lawyer can still log in, create cases and search; clip pages show the clip's index data (times, places, tags, edits) and say that the vault content needs the key.

## 5. Key management

### 5.1 Setting up a personal key

An applicant sets up their personal key after their password and second factor, before admission, so that the admin can check it. The browser makes the key pair, generates the key passphrase (100 random bits, shown as 20 characters of Crockford base32 in groups of 4, like an upload code, and forgiving case, dashes and look-alike letters in the same way) and the recovery key (260 random bits, 52 characters in the same alphabet). It shows both once, with a "save this now" message as for 2FA recovery codes: the passphrase to keep where the lawyer can type it (memory, paper or a password manager), the recovery key on paper with their recovery codes; the page says neither is the login password and no one can recover them. The applicant types both back before going on. The browser then uploads the public key and the private key encrypted under each. The server never sees the passphrase, the recovery key or the private key.

The passphrase is generated rather than chosen because a seized database lets anyone try passphrases offline against every lawyer's encrypted key, and one weak passphrase would give the group key, and with it the whole vault.

### 5.2 Admission: the admin checks the key

To the admission steps of SPEC.md §5, M5 adds one: the admin compares the applicant's safety number with them, through the bar directory's contact details (the channel already used to verify registration), and confirms it on the admission form, which sets the key's "admin confirmed at". The server refuses to admit an applicant whose current key is unconfirmed.

### 5.3 Key grant: a lawyer hands over the group key

An admitted lawyer without a key grant can log in, create cases and search, but cannot read the vault. Lawyers holding a grant see a list of admitted colleagues waiting for one, with who vouched for them (SPEC.md §5).

To grant, a lawyer compares the newcomer's safety number with them directly (by phone or in person; the page says not by email or message through the archive) and confirms. Their browser opens the group private key with their own personal key, seals it to the newcomer's public key, and uploads the grant. The server accepts it only if the newcomer is admitted and unsuspended, their current key is admin-confirmed, the granting lawyer holds a grant, and the grant covers every group key not yet retired (during a rotation, the old and the new one), in one request. Each grant writes an audit entry naming both lawyers.

So a grant needs both the admin and a lawyer, each comparing the safety number through their own channel. The admin's part is enforced by the server; the lawyer's part is cryptographic, since only a lawyer's browser can seal the group key. Against a database writer, the lawyer's own comparison catches a swapped newcomer's key. A signing key for the admin, so that lawyers' browsers could check the admin's approval themselves, is left for later (§11).

**The first lawyer.** While no group key exists, the first lawyer to unlock after admission makes it in their browser and grants it to themselves (an audit entry); the server accepts a new group key without a rotation only when no lawyer who is not revoked holds a grant on any group key (§5.7). Until a group key exists, the upload page is closed (§10).

### 5.4 Suspension and reinstatement

Nothing changes in the keys. A suspended lawyer is refused every lawyer page and request, as today, including the encrypted data, the sealed keys and a request carrying a data key (§6.3); with their key but without the data, they read nothing. Reinstatement restores access with the same keys. Rotating at suspension would make reinstatement a full new grant, while SPEC.md §5 has suspension remove access "and nothing else"; an admin who fears for the keys revokes instead.

### 5.5 Revocation and rotation

**Why.** While a lawyer works, the group private key is in their browser, and a lawyer who knows how can copy it. Revocation deletes their grants and retires their personal key, but cannot delete a copy, which opens every data key sealed to that group key, if they ever obtain the database. A rotation replaces the group key and reseals every data key to the new one, after which nothing in the current database opens with the old key. It runs after each revocation, after a suspected exposure of any lawyer's passphrase or recovery key, and for the change to post-quantum keys (§2).

**Why in a browser.** Resealing means opening each data key with the old group private key. The server must never hold that key, so a lawyer's browser does it, and the server only stores the result.

**Drawing lots.** Every role in a rotation goes to a lawyer the server draws at random (Python's `secrets`), never to whoever arrives first, so lawyers acting together cannot arrange who does what. A lawyer is **eligible** for a draw if they hold a grant on the group key concerned, are not suspended, have unlocked their key within the last 7 days (each unlock that the browser has checked, and reports, sets the day on their key, nothing finer; fetching the encrypted key does not count, so a lawyer who has lost their passphrase stops being drawn), and, within one attempt, do not already hold another of its roles. Lawyers are compared as people, not keys, so a lawyer with a new key is still the same lawyer. An attempt starts only once at least 3 lawyers are eligible on the current key (enough for a rotator and two checkers), counted when its rotator is drawn, so that a nearly empty pool never turns a draw into first come, first served; until then the start is retried each day, and the admin is alerted after 7 days without enough. A later draw within the attempt (a checker, a redrawn slot, a confirmer) that finds no eligible lawyer abandons the attempt (step 8), and a new one starts when 3 are eligible again: in a small archive, a failure no one else can confirm then works like a dispute, a delay and never a stuck attempt. Each attempt draws afresh: no one is barred from later attempts. A lawyer who keeps disrupting rotations shows up in the admin's alerts, by name, and the admin can suspend them, which takes them out of every draw at once.

**The steps.**

1. **Request.** Revoking a lawyer (always, even one who holds no grant at that moment: they may have had the group key and lost their grant since), or a lawyer reporting that their own key may have been exposed (which also replaces that key, §5.6; the admin cannot report it for them, since that would let the admin replace a key alone, and the admin's answer to a suspected exposure is revocation), starts a rotation of the current group key, which becomes `rotation_requested`, with an audit entry. If a rotation is already running, its current attempt is abandoned (step 8) and a new attempt drawn: a lawyer revoked mid-rotation may already hold the new key.
2. **Rotator.** The server starts an attempt and draws its **rotator** among the lawyers eligible on the current key. At their next vault page, their browser makes a new group key pair, seals its private key to every lawyer key holding a grant on the current group key (suspended lawyers included, so that reinstatement needs nothing new), and uploads the key and grants in one request. The server accepts it only if the grants cover exactly the lawyer keys holding a grant at that moment, under the lock below; otherwise the browser fetches the list again and retries. The new key becomes `checking`, recording its attempt and rotator, and the old one `retiring` (an audit entry).
3. **Reseal.** Only the rotator's browser reseals. It fetches, in batches, the data keys sealed to the old key, opens each, checks it against its key check value, seals it to the new key and uploads it. The server stores an uploaded sealed key only if the item has no sealed key for the new group key yet (one per item and public key, inserted under the lock), so it never replaces one the server sealed itself at a text edit or upload, and two uploads of the same item cannot both land; it records the rotator as its sealer, and ignores items deleted meanwhile. The old sealed keys stay. If the tab closes, the rotator's browser carries on at their next vault page. If the attempt has not finished resealing 3 days (configurable) after the rotator was drawn, it is abandoned (step 8).
4. **Checkers.** When nothing is left to reseal, the server draws two **checkers** for two slots, among the lawyers eligible on the new key, other than the rotator, and never the same lawyer for both. Drawing only now means no one can time it. A slot with no result 3 days (configurable) after it was filled is drawn again, among eligible lawyers other than its previous holder, and the admin is alerted with that holder's name, so one absent lawyer holds up the attempt by 3 days at most. Suspending a lawyer replaces, under the group key lock, every unreplaced role they hold in a running attempt, which is drawn again: a checker's slot even if they passed, and a confirmer (a suspension often means the admin doubts that lawyer); a rotator still resealing abandons the attempt (step 8), while one whose reseal is done stays recorded, since the checks judge their work. An attempt never ends with fewer than two passed checks.
5. **Check.** On any vault page a checker opens, their browser does this without any action from them: it opens their grant on the new key and checks it against the new public key, then fetches every data key the rotator sealed (not those the server sealed itself) and checks each opens and matches its key check value. It fetches no ciphertext, so the checker sees no clip content outside their cases. It reports a pass, or a failure naming the items that failed.
6. **Confirming a failure.** One failure report does not reject the attempt, since a dishonest checker could otherwise block every revocation. The server draws a **confirmer**, eligible as for a checker but other than the rotator and both slot holders, to check the named items (redrawn, as a slot is, after 3 days without a result, with an alert). If they fail again, the attempt is rejected (step 7). If the confirmer finds them sound, one lawyer's word stands against another's: the attempt is abandoned (step 8) and the admin is alerted naming both. If no confirmer can be drawn, the attempt is abandoned the same way, and the alert names the checker whose failure no one could confirm. If both slots report a failure, two lawyers drawn independently agree, and the attempt is rejected at once: a rejection loses nothing. A confirmer goes with the failure they were confirming: if the checker who reported it loses their slot (a suspension), the confirmer's role ends too, and the new checker's result decides afresh.
7. **Rejection.** The new key becomes `rejected`: its grants and sealed keys are deleted, the admin is alerted, and an audit entry names the rotator and the lawyers who found the failure. The old key returns to `rotation_requested`, and a new attempt is drawn.
8. **Abandoning.** The new key, if one was made, becomes `abandoned`, its grants and sealed keys are deleted, an audit entry says why (the rotator's time ran out, a check was disputed, a later draw found no one, a new revocation or exposure, or the archive started over, §5.7), the admin is alerted (except when the archive started over, which has its own entry), and the old key returns to `rotation_requested` for a new attempt.
9. **End.** When both slots hold a passed check from a lawyer who is not suspended, under the lock below, the server deletes every sealed key and grant on the old key, sets it `retired` and the new key `current`, and ends the rotation (an audit entry). From then on, the old key opens nothing in the database.


**The key check value.** When the server first seals an item's data key (at upload, at a text edit, or when encrypting existing data), it also stores BLAKE2b-256, keyed with the data key, of the label `fa-keycheck-v1`. Only the server writes key check values. It reveals nothing about the data key, but a browser that opens a sealed key can tell whether what it found is that item's key: this lets a checker detect junk without fetching content, and lets the rotator detect a corrupt sealed key before resealing it. Every comparison with it is made in constant time.

**Uploads and edits during a rotation.** Ingest encrypts a file and holds its data key in memory until it records the file; it seals the data key at that moment, in the same transaction, to every group key in `current`, `rotation_requested`, `checking` or `retiring` (so during a rotation, to both), taking the same lock as steps 2, 3 and 9. Texts are sealed the same way when written. Retiring a key therefore cannot overtake a file being recorded: a file encrypted for minutes before a rotation ends is still sealed to the key that becomes current, and one recorded during an attempt that is then rejected or abandoned still has its sealed key for the old one. The lock is a `select_for_update` on the group key rows (SQLite already runs each transaction as `IMMEDIATE`, so writers queue).

**What the checks protect against.** Resealing can only destroy, never reveal: junk sealed keys, once the old ones are deleted, would leave the vault unreadable. The server's operator could destroy the vault anyway by deleting files; backups are the defence against that. The checks defend against lawyers. Two lawyers acting together can never get the old sealed keys deleted with junk in place: two passed checks from two lawyers other than the rotator are needed, an honest checker's failure is either confirmed by a third lawyer drawn outside both slots or ends the attempt, and no role goes to whoever asks first. Three acting together need one drawn as rotator and the other two drawn into both slots: 1 in 10 per attempt with five eligible lawyers, about 1 in 1,140 with twenty, assuming each draw is uniform over the same pool. Since no one is barred from later attempts, three who keep forcing new attempts (by stalling or disputing) get a fresh draw each time, so their odds add up; each forced attempt alerts the admin, naming those involved, and the admin's suspension of them is the defence. Lawyers acting together also stay eligible by unlocking each week, while quiet honest lawyers drop out of the pool, which improves the odds of those acting together.

**Limits.**

- The files are not re-encrypted (2 GB each, and the server would need each data key from a browser); only the sealing of their keys changes. The revoked lawyer still knows the data keys of clips they opened, and may hold those clips anyway.
- Until an attempt ends, old sealed keys remain, for new uploads too, so the revoked lawyer together with a breach could still open them; the window lasts until the reseal is done and two checkers have been on a vault page. With fewer than three eligible lawyers, no attempt starts: the old sealed keys stay until enough lawyers hold a grant and have unlocked recently.
- A dishonest lawyer can delay a rotation by stalling as rotator or sitting on a slot (3 days each), or by disputing a check, and can do so again in later attempts; each time the admin is alerted with their name, and suspending them ends it.
- Backups made before the rotation ended still hold the old sealed keys. Keeping backups only for a limited time belongs to deployment.
- The rotator's browser opens every data key, not only its lawyer's cases. That gives it nothing it could not do already: any group key holder can (§1).
- The list of lawyer keys a new group key is sealed to comes from the server; a database writer could add a key of their own at that moment (§11).

### 5.6 Recovery

- **Forgotten key passphrase, recovery key at hand**: the browser opens the second copy of the private key with the recovery key, generates a new passphrase, shows it once and replaces the stored copy. The key, its safety number and its grants stay the same, and the recovery key stays valid. Copies of the database taken earlier still hold the old encrypted copy, which the old passphrase opens.
- **A passphrase or recovery key that may have been seen**: the lawyer reports it from their own account. Since they still hold their key, their browser makes a new personal key as in §5.1 (with a new passphrase and recovery key, shown once), reseals their case labels' data keys to it, and uploads both. The server accepts the upload only if it covers exactly the labels sealed to the old key at that moment, in one transaction under the group key lock, which case creation (§7.4) also takes and in which the last-holder check below is made (otherwise the browser fetches them again and retries, so a case created meanwhile in another tab keeps its label); in the same step it retires the old key and deletes its grants (an audit entry), and requests a rotation (§5.5). The response clears the browser's stored keys, and until the new key has the admin's confirmation and a lawyer's grant (§5.2, §5.3), the lawyer gets nothing from the vault (§3.3) and cannot report again, so reports cannot be repeated to keep abandoning rotations. The report is refused while the lawyer is the last unsuspended, unrevoked lawyer holding a grant (a suspended holder could not grant the new key, and might be revoked), with a message to have the key granted to another lawyer first: otherwise it would leave no one able to read the vault. Someone who saw it could otherwise open the private key from any copy of the database, then the grant, then the group key.
- **Both lost**: the lawyer can still log in (password and second factor do not depend on the key) and starts a key reset from their own account, which retires their key and deletes their grants (an audit entry). They set up a new personal key (§5.1), the admin confirms its safety number through the bar directory's contact details (§5.2), and a lawyer grants the group key again (§5.3). What was sealed only to the old personal key is lost: case labels show as unreadable (and from M6, their messages). The admin alone cannot reset a lawyer's key, so cannot destroy their case labels or messages.
- **Lost password or second factor**: SPEC.md §13 still leaves this open, and M5 does not add it. M5 changes what is at stake: whoever got into a lawyer's account, the admin included, would still lack the key passphrase, so would see no footage, context, contact details or case labels, only the case areas and time windows and audit entries, which the admin can already see.

### 5.7 No one left holding the group key

If every lawyer holding a grant were revoked or reset their key, uploads would be sealed to a key no one holds. So:

- Uploads are open only while at least one lawyer who is not revoked holds a grant on the newest group key in use (§3.3); otherwise the upload page is closed, as before the first group key (§10).
- The admin's revocation page warns, before confirming, when the lawyer is the last one holding a grant, and says that what the vault holds will become unreadable for good. Revocation is still allowed: it may be urgent.
- Once no lawyer who is not revoked holds a grant on any group key, the next admitted lawyer to unlock makes a new group key as the first lawyer did (§5.3), with an audit entry saying the archive started over; the earlier key still `current`, `rotation_requested` or `retiring` becomes `retired`, a `checking` one `abandoned`, and any running rotation and attempt end, so ingest seals to the new key alone. Everything sealed to the earlier keys stays, unreadable, until its clips are deleted.

## 6. Files: evidence copies and proxies

### 6.1 Encrypting at ingest

Ingest makes the evidence copy and proxy as today, then stores each through `vault.store_file`, which draws a random data key, encrypts the file with libsodium's secretstream in 64 KiB chunks, each carrying the clip's id and the file's kind as additional data and the last marked final, and returns the file's name and its data key, which the worker holds in memory. `record_file` then seals the data key and stores the key check value, in its transaction (§5.5). Ingest's temporary plaintext files are deleted as today (§9).

### 6.2 Reading, byte ranges and size

Secretstream decrypts in order: it cannot start at chunk N. Each request for a byte range therefore decrypts from the start of the file and discards what comes before the range. Measured on the development server (2 vCPU) during research: 761 MB/s, so about 0.07 s to reach the end of a 50 MB proxy, and 2.6 s to resume a 2 GB evidence copy near its end. That is acceptable for previews and for the rare resumed download. A format that can seek would mean independent encrypted chunks, which is a construction of our own (no maintained Python library offers it), so it is ruled out by the hard rule against custom cryptography (ADR 0008).

`vault.open_file(clip, kind, data_key)` returns a read-only file object that decrypts as it is read, whose `seek` restarts decryption and skips to the position, and which carries the plaintext `size`, computed from the stored file's size. It raises an error, which ends the response, when a chunk fails to authenticate, when the file ends without a final chunk, or when anything follows the final chunk: a cut-off or altered file is never served as complete. Such an error is logged by the file's random name only. `open_or_404` takes the object's `size` instead of `os.fstat(fileno())`; `file_response` is unchanged.

### 6.3 Handing the data key to the server

A `<video>` element and a download link cannot add headers to their requests, and a key in a URL would end up in web server logs. So the clip page's script opens the data key of the clip's proxy and posts it to a new view on the clip (case and match checked as for the preview, and CSRF-protected like every form); the evidence copy's data key is posted only when the lawyer clicks to download. The server checks the key against its key check value, stores nothing, and sets a cookie per file:

- `HttpOnly` (page scripts cannot read it back), `Secure`, `SameSite=Strict`;
- `Path=/lawyer/cases/<case>/clips/<clip>/`, so it is sent only with that clip's preview and download in that case;
- expiring after 2 hours, like the preview and download logging windows.

The preview and download views read it, and refuse with 403 when it is missing or wrong. The cookie gives nothing on its own: each request still needs the lawyer's login and a usable case of theirs that matches the clip, so it is useless to another lawyer in the same browser and to a suspended one. A resumed download after the cookie expired needs the clip page opened again. Research confirmed Chrome sends such a cookie with a `<video>` range request; Firefox, Safari and resumed downloads are tested in the first implementation step, and if one fails, the fallback is a short-lived entry in the cache, keyed by a random id in the cookie (the data key held in the cache, never the database).

The cookie is written to the lawyer's disk like other cookies, which is within the lawyer's device (§4). Django's error reports hide these cookies (it treats names containing "key" as sensitive), and the view that takes the key marks the posted field and its variable as sensitive too; nothing sends error reports today, and SPEC.md §13's open question about them still applies. The 2-hour expiry is kept by the browser: a saved cookie value would go on working, but gives nothing the lawyer could not open again, and every request still passes the login, case and match checks.

## 7. Texts

### 7.1 Context and contact details

Each text gets its own data key; it is padded to a multiple of 256 bytes (so its length shows only roughly) and encrypted with XChaCha20-Poly1305, with the clip's id and the text's kind as additional data. The data key is sealed to the group key (for lawyers) and to the clip's code holder key (for the code holder). Editing writes a new data key and ciphertext over the old row, so old values are still never kept (SPEC.md §5).

Lawyers: the clip page carries the ciphertext and the sealed data key; the script opens and shows them. The server decrypts a text only for a code holder, during their request (§7.2).

### 7.2 Code holders

The upload code (140 random bits) is already strong enough to be key material. When the uploader confirms the form, the server derives a 32-byte seed with BLAKE2b-256 of the label `fa-codeholder-v1` followed by the code as `clips.upload_codes.normalise` gives it, makes the key pair from it, and stores only the public key on the clip. The upload code's lookup hash (HMAC-SHA-256 with the server secret) is unrelated to this seed, so the stored hash does not reveal the key. The stored public key does let a copy of the database test a guessed code, without the server secret, which is harmless at 140 bits; `clips/upload_codes.py`, which says a copy of the database cannot test guesses, is corrected when this is built.

A code holder's every visit already sends the code in a form submission. During that request the server derives the private key, opens the code holder's copies of the data keys and shows the texts; it keeps nothing. Without the code, nothing addressed to the code holder can be opened, by the server or anyone else. Nothing changes for code holders, and the code still never travels in a URL, cookie or session (SPEC.md §5).

### 7.3 Messages (M6)

M6 builds messages on the same pattern: a message from a lawyer has its data key sealed to the code holder key and to that lawyer's personal key; a reply, to the code holder key and the personal key of the lawyer addressed. Other lawyers cannot read them. A lawyer who loses both passphrase and recovery key loses their messages, like their case labels.

### 7.4 Case labels

When a lawyer creates a case, the server encrypts its label like a text (padded, with the case's id as additional data) and seals the data key to the lawyer's personal public key; it sees the label once, in the form submission. Pages listing cases show the label through the script. The admin never sees case labels, as today. A lawyer whose personal key was reset sees "label unreadable" on older cases.

## 8. What stays readable on the server, and why

| Data | Readable? | Why |
|---|---|---|
| The index (times, cells, tags, edits, hash record, status) | Yes | Matching and the code holder's form need it; it is limited to what is harmless if exposed (SPEC.md §6). |
| Case time window and area | Yes | Matching runs on the server at every search, clip page, preview and download; the admin reads them to approve a larger case. Encrypting them would also mean encrypting the audit log's clip ids, which together with the index reveal roughly the same thing. |
| Audit log | Yes | Alerts count its entries; the admin reads all of it; views and previews are deduplicated and closing by disuse read from it; the tag history is drawn from it. |
| Lawyer's reason | Yes | The admin must read it; it is deleted once the admin decides. |
| Two-factor secrets | Yes | See below. |
| Public keys, sealed keys, key check values, rotation checks | Yes | Not secret. |

**Two-factor secrets.** django-otp has no supported way to encrypt authenticator secrets or hash recovery codes. A recovery code logs in as well as the authenticator secret, so encrypting one without the other gains nothing; hashing recovery codes means changing how django-otp checks them, which is close to writing our own authentication. With M5, getting into a lawyer's account no longer gives the vault, so what someone with a copy of the database and a cracked password gains is the index, cases and audit entries added after the copy, until the password changes; as the admin, they could also suspend, revoke or admit, though an admitted impostor still needs a lawyer's key grant. The better defence is encrypted backups at deployment (SPEC.md §12 Later). This answers SPEC.md §10's question: not in M5.

The residual risk in SPEC.md §3 stays, reworded: a seized server shows which lawyer looked into which incident and which clips they opened, but not what any clip shows.

## 9. Quarantine and ingest: the weakest link

Quarantine is the one place the server must hold readable footage, and it holds the most sensitive version of it: the original, with its precise GPS, device make and model and creation software, which identify the filmer and, for a clip filmed from a window, their home (ADR 0002).

**What lies there, and for how long.**

- From upload until the uploader confirms the form: at most 2 hours (configurable), after which an unconfirmed original is deleted (SPEC.md §8).
- From confirmation until ingest ends: at most about 1.5 hours with all retries (SPEC.md §9), then the original is deleted.
- While ingest runs: also the evidence copy and proxy as temporary plaintext files, until they are encrypted into the vault and deleted, and the data keys in the worker's memory until they are sealed.
- In the database during that time: the clip's index data, and the GPS position snapped to its cell once ingest reads it. The precise position is never written to the database (ADR 0002), but it is in the original file until the file is deleted.

**Deleted is not erased.** Deleting a file removes its name, not necessarily its contents: on an SSD (which moves data around to spread wear) and on ordinary file systems, the old blocks can survive until reused, and a disk image can recover them. The same holds for SQLite's freed pages and its write-ahead log. So a copy of the disk can hold originals, with their GPS, from long before the last few hours, and the plaintext the vault held before M5. Only encrypting the whole disk, or keeping quarantine in memory, prevents this; this PR makes one of them a requirement before any real use (SPEC.md §12 Later: deployment). The `encrypt_vault` command (§10) also runs SQLite's `VACUUM`, which rewrites the database without its freed pages.

**Who can reach it.** Nothing in quarantine is served over the web (SPEC.md §8, §9). The folder is private to the archive's user on the server. ffprobe and ffmpeg read the original, so a flaw in them is the most likely way for a malicious upload to run code on the server; the archive accepts only the container formats phones record in, limiting which parts of ffmpeg ever run, but ffmpeg from Ubuntu's `universe` gets security fixes only through Ubuntu Pro (an open follow-up). Such code would run as the archive's user, could read quarantine and, while it ran, everything else the server reads (attacker type 4).

**What each attacker gets.**

- At rest, at a random moment: the originals uploaded in the last few hours, with their GPS, and, without full-disk encryption, any older ones the disk has not yet overwritten.
- Passive on the running server, for a period: every original uploaded during that period.
- Active: the same, plus everything §1 says.

**What could make it smaller** (beyond the deployment requirement above, none is in M5):

- A RAM-backed quarantine and temporary folder (tmpfs): nothing readable survives a power-off, and nothing is left in disk blocks. Up to 2 GB per upload in RAM limits how many uploads can wait at once; swap must be encrypted or off.
- Full-disk encryption on the server: protects a powered-off or removed disk, and old blocks along with it, not a running server.
- A shorter confirmation window than 2 hours.
- Ingest on a separate machine with no web access, fed only through the queue: a flaw reached through the web front end would no longer reach quarantine, and a flaw in ffmpeg would no longer reach the web front end or the database.
- Encrypting originals to the group key at upload, with ingest asking a lawyer's browser for the key: the server could no longer process uploads on its own, and uploads would wait for a lawyer to log in. Rejected for now.

## 10. Moving existing data, and closing uploads without a group key

- **Uploads without a group key**: while no group key exists, or no lawyer who is not revoked holds a grant on the newest one in use (§5.7), the upload page says uploads are not open, and the server refuses uploads, so ingest never has to store a file it cannot encrypt. In a deployment, the first lawyer is set up before uploads open.
- **Plaintext already in the vault** (development data only): a management command, `encrypt_vault`, encrypts each plaintext file and text to the current group key, writing each file to a new name and switching the row over in one transaction, so a crash leaves either the old or the new state, never a half-written file in use; run again, it skips what is already encrypted, and finishes with `VACUUM`. Code holder copies of existing texts cannot be made, since the server does not know the codes: the code holder's form shows them empty until the code holder enters them again, and the command says how many it left without one. Case labels cannot be sealed to their lawyer's key without that lawyer; the command seals each to the lawyer's personal key if they have one and leaves the others, saying how many.

## 11. Known limits

- **JavaScript served by the archive** (attacker type 4): the server that stores the ciphertext also sends the code that holds the keys, so a compromised server can steal lawyers' keys. Every encryption in a web page has this limit; browser extensions that check the served code (WEBCAT, Code Verify) are not usable for an ordinary site yet. A separate application holding the keys, as SecureDrop does, is the answer if this ever becomes the main threat.
- **Ingest sees every upload** in plaintext (§9).
- **No case scoping by encryption** (§1).
- **Nothing proves who wrote a vault item** (§1): the server, holding the group public key, can encrypt contents of its own. The hash record remains the check on the evidence copy.
- **A database writer can swap the group public key** (attacker type 3): replacing it with a key of their own, sealed to every lawyer's key in new grants, would make new uploads readable to them, and lawyers' browsers would see a valid key. They could also add a key of their own to the list a rotation seals to (§5.5). Next step: a signing key for the admin, who signs each confirmed lawyer key and each group key, with lawyers' browsers refusing to seal to, or accept, unsigned ones. It needs the admin to hold a key, with its own recovery, and lawyers' browsers to know the admin's public key from a source the server cannot swap.
- **Protection at rest rests on every lawyer's key passphrase** (§5.1), which is why it is generated.
- **Post-quantum**: X25519 until PyNaCl offers X-Wing (§2).
- **Backups made before a rotation ended** still open with the old group key (§5.5).
- **Clips already downloaded** stay with the lawyer, whatever happens to their access.
- **Keys in `sessionStorage`** reach the lawyer's disk and are exposed to a cross-site scripting bug (§4).
- **Deleted files survive on disk** without full-disk encryption (§9).

## 12. What M5 is done when

Proposed for SPEC.md §12 (this PR adds it):

- tests show that after upload and ingest, the vault's files and the database hold no plaintext of the evidence copy, proxy, context, contact details or case label;
- that a preview and a download with the right data key serve the right bytes, byte ranges included (a range near the end, a suffix range, an unsatisfiable range), that without it, or with a wrong one, they serve nothing, and that a cut-off or altered file, or one moved to another clip or kind, is never served as complete;
- that no data key or private key is stored in the database or the cache, or written to a log;
- that a code holder reads their own context and contact details with the code, and nothing can be opened without it;
- that the server refuses to admit an applicant whose key the admin has not confirmed, and refuses a key grant from someone without one, to someone not admitted or suspended, to a key not admin-confirmed, or not covering every group key in use; that grants, key resets, rotations and their checks each write an audit entry;
- that revocation requests a rotation, and a revocation during one abandons the running attempt; that the rotator and checkers are drawn at random among eligible lawyers, compared by lawyer and not by key, and that only the rotator reseals, never replacing a key the server sealed; that a file recorded while a rotation runs is sealed to both keys and stays readable whichever way the attempt ends; that old sealed keys are deleted only after two passed checks by two different lawyers other than the rotator; that a slot without a result after 3 days is drawn again; that no attempt starts with fewer than three eligible lawyers; that a single reported failure rejects the attempt only once a confirmer outside both slots agrees, two failures reject it at once, and a disputed check, or a later draw finding no one, abandons it, keeps the old sealed keys and alerts the admin; that a checker suspended before the end loses their slot; that a lawyer whose key holds no grant gets nothing from the vault;
- that uploads are refused while no lawyer who is not revoked holds a grant on the newest group key;
- that every view handing over encrypted data is for lawyers only and case-scoped where it concerns a clip, that a person's own encrypted key goes only to them, as an applicant or lawyer, that all of these are refused to a suspended lawyer, and that every lawyer page carries the Content Security Policy;
- that values made by libsodium.js (sealed boxes, seeded key pairs, secretstream, authenticated encryption, Argon2id, BLAKE2b with a label), stored in the repository as test vectors, open with PyNaCl, and the reverse;
- and, checked by hand in Firefox and Chrome, that a lawyer can set up a key, unlock, read a clip's texts, watch its proxy and download its evidence copy, whose SHA-256 matches its hash record, and that a rotation runs and is checked.

## 13. Dependencies and changes outside the vault

- **PyNaCl 1.6.2** (Apache-2.0) in `requirements.txt`.
- **libsodium.js 0.8.4, sumo build** (ISC), two files copied into the repository with their licence and checksums, as for the map's libraries.
- **Content Security Policy**: the map page's strict policy on every page (no template has inline script or style), plus `script-src 'self' 'wasm-unsafe-eval'` on lawyer pages and on the pages where an applicant sets up their key.
- **Audit log**: new actions for key grants, key resets, rotation requests, attempts, check results, rejections, abandons and ends, and starting over.
- **Alerts**: a rejected rotation, a disputed check, a rotator's time running out, no lawyer eligible for a draw, and a key grant that fails its recipient's check.
- **Accounts**: key setup for applicants, the confirmed safety number at admission, the lawyer's key reset, the waiting-for-a-key list, the warning before revoking the last key holder.
- **CLAUDE.md**: the hard rule on cryptography names libsodium.js as well as PyNaCl (this PR).

## 14. Build order (to be proposed in detail once this is approved)

Roughly: (1) the dependencies, test vectors and a check of the data key cookie in Firefox and Safari; (2) encrypted files and texts in the vault behind its existing functions, with the group key, data keys and key check values, and the `encrypt_vault` command; (3) personal keys, unlocking and key grants, with the admission change; (4) code holder keys; (5) case labels; (6) revocation, rotation and its checks, and recovery. Nearly every step changes encryption, accounts or the audit log, so nearly every PR is reserved to the user, each with an Opus review.
