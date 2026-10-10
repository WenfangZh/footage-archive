# SPEC: footage archive for the defence (learning build)

Working title. This file is the source of truth for what we're building and why. If something here seems wrong or ambiguous, ask before guessing; decisions we change get written back here.

## 1. Purpose

A place to upload short phone videos of arrests and police operations at protests, so that defence lawyers can find footage relevant to their clients (mainly minors who are innocent or falsely accused) and check it against police reports (procès-verbaux).

Two principles shape everything:

- **Purpose limitation.** Footage is available only to verified defence lawyers, and only for a specific case. The archive is not a general pool of evidence.
- **Privacy by design.** The people most at risk are those who filmed and those in frame, mostly minors. We collect as little as possible, strip what identifies filmers, and aim for an operator who cannot read the footage at all.

## 2. Status

- Learning build: no real footage, no real users, nothing deployed beyond a local machine.
- Public GitHub repository, for visibility: everything committed is public and permanent, and the README says plainly that this is a learning build.
- Test data: our own recordings plus synthetic videos generated with ffmpeg.
- We still design for real stakes, since retrofitting security is far harder than building it in. Before any real use: external security review, legal review, and a partner organisation.

## 3. Threat model

| Threat | What's at risk | Main defence |
|---|---|---|
| Breach, seizure or compelled disclosure of the server | Everything stored | Data minimisation; operator holds no readable footage (target state, M5) |
| People trying to identify filmers or protesters | Identities, home locations | Metadata stripping, no accounts linking uploads, no public views |
| Lawyers acting for other parties | Footage used outside its purpose | Admission by vouching, case-scoped search |
| Phished or compromised lawyer account | Bulk access | Mandatory 2FA, case scoping, access logs, volume alerts |
| Malicious uploads | Lawyers' machines, archive credibility | Validation, quarantine, never executed, size caps, rate limits |
| Pressure on the admin | Anything the admin can see | Admin manages accounts, not content |

Known limit outside our control: once a lawyer files a clip in a case, every party to that case can see it.

## 4. Non-goals

- No public views of footage: no gallery, feed, share links or embeds. Ever.
- No automated identification of people (no face recognition).
- No custom cryptography, password hashing or authentication.
- No social features.
- No real footage or real users during the learning build.

## 5. Roles

**Anonymous uploader.** No account. Uploads a video with optional fields and receives an upload code, shown once. With it they can come back to edit fields, add files, and read or answer messages from lawyers. Codes carry at least 128 bits of randomness in a human-friendly format (e.g. grouped base32 or a word list) and are stored only as a keyed hash (HMAC-SHA-256 with a server secret), so they can be looked up without being stored. Failed code attempts are rate-limited. A lost code cannot be recovered.

**Defence lawyer.** Invitation only. Admission requires:
1. verification that the person is an avocat, using the bar directory and confirming through the directory's contact details, never those supplied by the applicant;
2. a vouch from a trusted defence organisation or an existing lawyer member;
3. admin approval.

2FA mandatory. Access is case-scoped (§7). Admin can suspend or revoke at any time. In the learning build, steps 1 and 2 are recorded as fields on the account (`verified_via`, `vouched_by`), not integrated with anything external.

**Admin.** Manages invitations, roles, suspensions, flags and deletions. 2FA mandatory. Target state: cannot view footage or vault contents.

Optional uploader accounts (from the first draft) are deferred: see §13.

## 6. Data: index vs vault

The server can only filter on what it can read, so filterable fields live in a search index restricted to data that is harmless if exposed. Everything else lives in the vault.

- **Index (server-readable):** capture time (precise), coarse location (snapped to a 50 m grid; the grid size is a setting), duration, resolution, tags, upload time, processing status, hash record.
- **Vault (target: encrypted to lawyer keys in M5):** evidence video file, proxy, attachments, precise location, free-text context, any contact details the uploader chose to give, messages.

Before M5 the vault is plaintext, but all vault access goes through a single storage interface so that encryption can be added without touching callers.

## 7. Lawyer access (case-scoped)

- A lawyer creates a case: a label (no client names), plus a time window and area taken from the police report.
- Search returns only clips whose capture time and coarse location fall inside that case (with configurable padding). There is no global browsing.
- Lawyers watch low-res proxies and can download the evidence file with its hash record.
- Every search, preview and download goes to an append-only audit log: lawyer, case, clip, action, time.
- Unusual volume (e.g. many downloads in a short period) raises an alert to the admin.
- A lawyer can send a message to the holder of an upload code. The uploader sees it on their next visit and decides whether to reply or identify themselves.

## 8. Upload

The interface is in French by default, with a visible switch to English (Django's built-in translation framework; see §11).

Everything is optional except the video:

- capture time: taken from metadata when present, shown to the uploader to confirm or correct;
- location: map pin or address, taken from metadata when present, shown to confirm or correct;
- tags: defaults below, plus custom tags;
- context: free text;
- contact details or social media handle: explicit opt-in, behind a plain-language warning that anyone who obtains this information could use it against them.

Tags are stored as stable, language-neutral keys and shown in the user's language (French and English for now). Default tags describe what can be seen, not conclusions, and their keys stay country-neutral so the list can travel; the French labels use the French terms.

| Key | English | French |
|---|---|---|
| `arrest` | Arrest | Interpellation |
| `use_of_force` | Use of force | Recours à la force |
| `officer_id_legible` | Officer ID number legible | RIO lisible |
| `officer_id_not_visible` | Officer ID number not visible | RIO non visible |
| `police_armband_visible` | Police armband visible | Brassard police visible |
| `no_police_armband_visible` | No police armband visible | Pas de brassard police visible |
| `plainclothes_officers` | Plainclothes officers | Policiers en civil |
| `minor_in_frame` | Minor in frame | Mineur à l'image |
| `injury_visible` | Injury visible | Blessure visible |
| `tear_gas` | Tear gas | Gaz lacrymogène |
| `kettling` | Kettling (crowd encircled and held by police) | Nasse |

Custom tags are stored as typed, without translation.

After upload, show the upload code once with a clear "save this now, it cannot be recovered" message.

Limits: cap by file size, not duration (default 2 GB, configurable). Rate-limit uploads; IPs may be used transiently for this (in cache, short TTL) but are never written to the database or logs.

## 9. Ingest pipeline (background job)

1. Store the upload in a quarantine area that is never served over the web.
2. Validate with ffprobe that it is a real video; reject it otherwise.
3. Compute SHA-256 of the file exactly as received.
4. Compute per-stream hashes of the encoded packets (ffmpeg stream copy, no decoding).
5. Extract metadata (ffprobe; exiftool optional). Capture time goes to the index. GPS goes to the index snapped to the grid, and to the vault at full precision. Device make and model, software versions and unique identifiers are discarded.
6. Remux: copy the encoded video and audio streams into a new container without decoding or re-encoding them, keeping only those streams (data tracks are dropped) and none of the container metadata. This is the **evidence copy**. It is never re-encoded.
7. Recompute per-stream hashes on the evidence copy. They must equal step 4, or the job fails loudly.
8. Generate a low-res H.264 proxy for previews. The proxy is never evidence.
9. Delete the quarantined upload: once step 7 passes, it is the only copy still carrying identifying metadata. Keep the hash record (results of steps 3, 4 and 7, with timestamps). It proves the frames are untouched, and lets a copy of the original (for example, still on the filmer's phone) be matched against what we hold.

If a job fails or exceeds its time limit, it is retried with backoff for up to 30 minutes. After the last failure, the quarantined upload is deleted immediately and the upload record shows a failed status, so an uploader who returns with their code knows to upload again. Uploads still waiting in the queue are never deleted for age; a backed-up queue alerts the admin instead.

Later: a trusted timestamp (RFC 3161) over the hash record.

## 10. Security requirements

- Django's auth with the Argon2 password hasher. No hand-written auth or crypto.
- Encryption only through PyNaCl (libsodium). M5 starts with a written design (key management: how a newly admitted lawyer gets access to existing clips, revocation, recovery) that is reviewed before any code is written.
- 2FA for lawyers and admin through a maintained package (propose options before choosing).
- Uploaded files are untrusted: validate them, never execute them, never serve them from quarantine. Attachments are limited to an allowlist (images, video, PDF).
- No IP addresses, user agents or uploader-supplied fields in application logs.
- The audit log is append-only: the application has no update or delete path for it.
- Secrets come from environment variables; `.env` is gitignored.

## 11. Stack

- Python 3.12 in a pyenv virtualenv (`footage-archive`, selected via `.python-version`), Django, server-rendered templates, minimal JavaScript. Function over looks.
- Django's built-in translation framework (its tools need GNU gettext, from apt): French by default, English available. Every user-facing string is translatable from day one.
- SQLite in development, Postgres later.
- ffmpeg and ffprobe from apt. Package installs (apt and pip) are done by hand, not by the agent.
- A simple background task queue (propose options; fewest moving parts wins).
- File storage behind Django's storage API: local disk now, S3-compatible object storage later.
- pytest with pytest-django. Test videos are generated with ffmpeg (synthetic `lavfi` sources with injected fake GPS and device tags), so tests never need real footage.
- Development on WSL2 (zsh, pyenv), with the repo in the Linux filesystem.

## 12. Milestones

Each milestone: plan first, then small steps, with tests green before moving on.

**M0: Skeleton.** Django project, settings from environment variables, translation set up (French default, English available), `.gitignore` (covering `.env`, `media_test/` and local media storage), pytest wired up, ffmpeg availability check, and a README with setup-from-scratch steps (pyenv included) that opens with a notice: learning build, not deployed, not for real footage.
*Done when* a fresh clone plus the README steps gives a running server and a passing `pytest`, and the README's first lines carry that notice.

**M1: Anonymous upload.** Upload form (§8) with the default tags in French and English, quarantine storage, size cap, upload code issued once and stored as a keyed hash, return-with-code to edit fields.
*Done when* tests cover code issuance, wrong-code rejection, editing, and oversize rejection, and the form renders in both languages.

**M2: Ingest pipeline.** Steps 1–9 of §9.
*Done when* tests show that (a) per-stream hashes match before and after stripping, (b) the evidence copy contains only video and audio streams and no GPS or device metadata, (c) a portrait video still displays upright, (d) a non-video file is rejected, (e) the proxy exists and the quarantined upload is gone, and (f) after a job's last failed retry, its quarantined upload is deleted and the record shows a failed status.

**M3: Roles.** Lawyer invitation and admission flow (§5), admin approval and revocation, mandatory 2FA, audit log model.
*Done when* tests cover each role's permissions, including that an unapproved or suspended lawyer sees nothing.

**M4: Case-scoped search.** Cases, matching, proxy previews, downloads with hash record, audit logging, volume alerts.
*Done when* tests show that a lawyer only ever sees clips inside their own case windows, and that every access writes a log entry.

**M5: Encryption.** Design document first, reviewed, then implementation behind the vault interface.

**M6: Upload robustness and messaging.** Resumable uploads (tus protocol), attachments (allowlist), lawyer-to-uploader messages through the upload code.

**Later.** Optional uploader accounts; trusted timestamps; verifiable excerpt export (per-packet hashes so an excerpt can be checked against the original); OCR of RIO numbers, lawyer-side only; native app; deployment; external security review.

## 13. Open questions

- Keep optional uploader accounts at all, given that they link uploads together?
- Discard the untouched original after hashing (current default), or also keep it in the vault?
- Keep precise GPS in the vault, or drop it entirely?
- Long term: stay standalone, or move to (or build on) GlobaLeaks for international use? Language-neutral tag keys keep that door open.
- Before any real use: hosting-provider data retention rules versus anonymity, GDPR legal basis and impact assessment (AIPD), who operates the service (an association?), and which defence organisations could vouch for lawyers.
