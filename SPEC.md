# SPEC: footage archive for the defence (learning build)

Working title. This file is the source of truth for what we're building and why. If something here seems wrong or ambiguous, ask before guessing; decisions we change get written back here. Terms are defined in GLOSSARY.md; decisions that are hard to reverse are recorded in `docs/adr/`.

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
| People trying to identify filmers or protesters | Identities, home locations | Metadata stripping, no accounts or codes linking clips, no public views |
| Lawyers acting for other parties | Footage used outside its purpose; exculpatory clips hidden or discredited | Admission by vouching, case-scoped search, count-based reports and tag removal (no single lawyer can hide a clip or a tag) |
| Phished or compromised lawyer account | Bulk access | Mandatory 2FA, case scoping with size caps, audit log, alerts |
| Malicious uploads | Lawyers' machines, archive credibility | Validation, quarantine, never executed, size caps, rate limits |
| Seizure of a code holder's phone or code card | The clip, its messages, the code holder's identity | Code card label never stored on the server, lawyers anonymous to code holders by default, lawyer tags never shown to code holders |
| Pressure on the admin | Anything the admin can see or do | Admin manages accounts, not content; acts on reports only by count; the admin's actions on accounts go to the audit log, where the lawyers concerned can read them |

Known limit outside our control: once a lawyer files a clip in a case, every party to that case can see it.

Known residual risk: cases (time window and area) and the audit log must be server-readable for matching and alerts, so a breach or seizure of the server reveals which lawyer looked into which incident and which clips they downloaded, in effect part of the defence's strategy. M5 looks at how much of this can be encrypted.

## 4. Non-goals

- No public views of footage: no gallery, feed, share links or embeds. Ever.
- No automated identification of people (no face recognition). The one exception: identifiers that can only belong to police or other public officials acting as such, like an officer's RIO number or a police armband, may be read automatically, for lawyers only. Anything a civilian could also carry or wear (a name tag, a face, clothing, a vehicle plate) is never identified automatically.
- No custom cryptography, password hashing or authentication.
- No social features.
- No real footage or real users during the learning build.

## 5. Roles

**Uploader, then code holder.** No account. The **uploader** uploads one video with optional fields and receives an upload code, shown once; one code covers exactly one clip. From then on the system knows only a **code holder**: whoever presents the code. That may be several people, or someone other than the filmer, and the system cannot tell them apart; messages from lawyers are written with that in mind. With the code they can come back to edit fields, delete the clip (its hash record is kept), and read or answer messages from lawyers. The fields as given at upload are kept as the **as-uploaded** values for stated time, stated location and tags; each later **edit** records which field changed and when. Old values of context and contact details are never kept. A stated location can be made vaguer from what is kept; a more precise one needs a new position. Codes are 28 random characters in Crockford's base32 (140 bits), shown in 7 groups of 4; case, dashes and look-alike letters are forgiven when typed. They are stored only as a keyed hash (HMAC-SHA-256 with a server secret), so they can be looked up without being stored. The code travels only in form submissions, never in a URL, a cookie or a server session, so the server never sees two clips opened from one browser. Failed code attempts are rate-limited: after too many wrong codes from one connection, no code is checked, not even a right one, until the window ends. A lost code cannot be recovered.

**Defence lawyer.** Invitation only: the admin sends the **invitation** by email to the person's own professional address, and its link works once and expires (default 3 days, configurable). Admission requires:
1. verification that the person is a practising lawyer registered with a French bar, using the bar directory and confirming through the directory's contact details, never those supplied by the applicant;
2. a vouch from a trusted defence organisation or an existing lawyer;
3. admin approval.

An invited person is an **applicant** until admitted. They log in with their email address; their account also records their name and their bar. While someone is an applicant, the admin can send them a new invitation link until they have set a password (it makes earlier links stop working), or decline them: a **declined applicant** cannot log in, and their name, bar and email address are erased, leaving the account only as an identifier for the audit log. Only an admitted, unsuspended **lawyer** can create cases or see anything about clips; a **suspended lawyer** sees nothing until reinstated. Throughout this spec, "lawyer" means the admitted, unsuspended kind. 2FA mandatory, set up as an applicant, before admission. Access is case-scoped (§7). Admin can suspend a lawyer at any time; suspension removes access at once and nothing else, and **reinstatement** undoes it. Admin can also **revoke** a lawyer, after an explicit confirmation step: revocation closes all their cases and can never be undone. Neither suspension nor revocation deletes anything the lawyer did: their audit entries stay, messages they sent stay visible to code holders (marked "this lawyer no longer has access"), and clips are unaffected; revocation removes only the account's means of logging in (password, second factor, sessions). A revoked lawyer's or declined applicant's email address no longer logs in and can be invited again; that makes a new applicant, who needs verification, a vouch and admission like anyone else, while the old account stays as it was. A revoked lawyer's address is kept, though not for logging in, so that the admin is warned before inviting it again. In the learning build, steps 1 and 2 are recorded as fields on the account (`verified_via`, `vouched_by`), not integrated with anything external.

**Admin.** Manages invitations, admissions, declining applicants, suspensions, reinstatements and revocations; approves cases larger than the caps (§7); deletes withdrawn clips (§7) and nothing else; receives **alerts** (unusual lawyer activity, §7, many failed logins for one account, §10, and a backed-up ingest queue, §9) on the admin's pages in the archive, not by email for now; can read the whole audit log and each clip's tag history. 2FA mandatory. Target state: cannot view footage or vault contents.

Optional uploader accounts (from the first draft) are deferred: see §13.

## 6. Data: index vs vault

The server can only filter on what it can read, so filterable fields live in a search index restricted to data that is harmless if exposed. Everything else lives in the vault.

- **Index (server-readable):** capture time (precise; both the metadata time and the stated time, see §8), location (the stated location and the metadata location, each as one grid cell: the stated location's cell is sized by its stated precision, the metadata location's by a precision the uploader chooses, ADR 0004; the grid is H3, its finest cells are resolution 11, about 50 m across, and that limit is a setting that can be made coarser but never finer, ADR 0003), duration, resolution, tags (with their source and removal proposals), as-uploaded values and edit records, upload time, clip status (processing, ready, failed, withdrawn), content warning and doubt marker, hash record. Upload time, edit times and the hash record's times are stored only to their 4-hour block (00h–04h, 04h–08h and so on, Paris time): an exact time could be matched against connection logs to find the uploader, and hashing runs minutes after upload.
- **Vault (target: encrypted to lawyer keys in M5):** evidence copy, proxy, free-text context, any contact details the uploader or code holder chose to give, messages. No location is stored at better than grid precision, anywhere: precise GPS shows where the filmer stood, which for a clip filmed from a window is their home.

Before M5 the vault is plaintext, but all vault access goes through a single storage interface so that encryption can be added without touching callers.

## 7. Lawyer access (case-scoped)

- A lawyer creates a case: a **case label** (no client names), plus a time window and area taken from the police report. A case belongs to exactly one lawyer and covers one incident: it is a search scope, not the legal proceeding. Co-counsel each create their own case; a client arrested twice means two cases. Each case also has a **margin** around its time window and area, fixed when it is created (by default 10 minutes and one ring of the grid's finest cells; each lawyer sets their own defaults). The area is a set of the grid's finest cells, made from a place and a radius; the coordinates typed to make it are not kept. A case's time window is at most 2 hours and its area at most 2 km across (its two farthest points, edge to edge), margin included (both configurable, but never higher than the **ceiling**); a larger case needs admin approval, and the request is logged. No case goes beyond the ceiling of 24 hours and 10 km across, margin included, even with approval: it is fixed in the code, so raising it takes a public change. The window and area are fixed once created: to change them, the lawyer creates a new case. A lawyer can close a case; a closed case allows no searches, previews or downloads, but stays in the audit log. Cases close automatically after a period without use (default 1 year, configurable).
- Search returns only clips that match the case: any part of the clip (stated time to stated time plus duration) overlaps the time window, and its stated location's cell overlaps the area, each widened by the case's **margin**. Results come in two groups: **core matches**, inside the time window and area themselves, then **margin matches**, found only in the margin. There is no global browsing.
- Lawyers watch low-res proxies and can download the evidence copy with its hash record. Alongside each clip they see its metadata time and location, its as-uploaded values and its edits; a stated time that differs from the metadata time by more than a threshold (default 15 minutes, configurable) is highlighted.
- Every lawyer action goes to an append-only audit log: case creation and closing, request for a larger case, search, preview, download, message, tag addition, tag removal proposal and report. Each entry records lawyer, case, action, time, and the clip where there is one (for searches, the numbers of core and margin matches instead). The admin's actions on applicants' and lawyers' accounts (each invitation link sent, admission, declining, suspension, reinstatement and revocation) go to the same log, each entry recording the admin, the person concerned, the action and the time. Entries are kept compact (keys and identifiers rather than text). The admin can read the whole log; each lawyer can read their own entries, including the admin's actions on their account; no one else can read any of it.
- Lawyers can add tags to a clip they can see, and propose removing any tag, including the code holder's. A tag is removed once three lawyers holding different cases have proposed removing it, and a removed tag comes back only by the same rule. Removal applies to what lawyers see: the code holder's own view and the as-uploaded values are untouched.
- Lawyers see each tag's source (code holder or lawyer, never which lawyer), shown by colour plus a short word ("code holder", "lawyer") so it does not rely on colour alone. Colours are configurable; defaults are blue for the code holder and orange, a pair that stays distinct for colour-blind readers. Lawyer tags and removal proposals are never shown to the code holder.
- The admin can view each clip's **tag history**: every tag added, proposed for removal and removed on that clip, by whom and when, to spot disputes over a clip's tags. It is drawn from the audit log and the clip's edits, not kept separately.
- Unusual volume (e.g. many downloads in a short period, or many cases created in a short period) raises an alert to the admin.
- A lawyer can report a clip. A report that the time or place is wrong reaches the code holder as a message. A doubt about authenticity gets no verdict from anyone: other lawyers whose cases match the clip see a neutral **doubt marker** ("a lawyer has raised a doubt about this clip"), with no free text, which could leak one lawyer's case to another. A report of inappropriate content (not protest footage, sexual content, footage posted to expose someone) cannot be judged by the admin, who cannot view clips, so it works by count: one report puts a **content warning** on the clip for other lawyers; a report from a second lawyer holding a different case **withdraws** the clip (hidden from all search, code holder messaged). The admin may delete a withdrawn clip; its hash record is kept. No single lawyer can hide a clip.
- A lawyer can send a message to the code holder of a clip. By default the code holder sees the sender only as "a verified defence lawyer"; each lawyer has a setting to identify themselves (name and bar) by default, and can override it per message. The code holder sees messages on their next visit and decides whether to reply or identify themselves. Messages are deleted a set time after the last message in their thread (default 6 months, configurable); lawyers are warned to save what they need into their own records.

## 8. Upload

The interface is in French by default, with a visible switch to English (Django's built-in translation framework; see §11). A visitor whose browser prefers English gets English until they use the switch; a choice made with the switch always wins.

Upload happens in two steps. First the uploader sends the file; it goes straight to quarantine, and a quick ffprobe read there checks that it is a real video (if not, it is deleted at once and no code is issued) and extracts only the metadata time and GPS to pre-fill the form (nothing is executed or served). Only the container formats phones record in are accepted (MP4/QuickTime, Matroska/WebM). A metadata time without a UTC offset, or a placeholder time (1904 or 1970), counts as no metadata time. Then the uploader fills in and confirms the fields below, and only then is the upload code issued. The full ingest (§9) runs after that. If the form is not confirmed within a set time (default 2 hours, configurable), the original is deleted and nothing is kept, not even a hash, since no clip ever existed.

Everything is optional except the video:

- capture time: the **metadata time** is read from the file and can never be changed by anyone; it is unreliable (phone clocks, time zones, re-saving apps) and trivial to forge, which is why it is kept as found. The uploader sees it and gives a **stated time**, pre-filled with the metadata time, which they may keep or replace. Lawyers see both;
- location: a **stated location**, given as a map pin or address (for now, coordinates; see §13) plus a precision (exact spot, street or neighbourhood), pre-filled from the file's GPS when present as an exact spot. It is kept as a grid cell of about 50, 130 or 340 m to match the precision. When the file has GPS, the uploader also chooses how precisely its position is kept as the **metadata location**: a cell of about 50 m (pre-selected), 130 m or 340 m. The code holder can later make it coarser, never finer (ADR 0004);
- tags: defaults below, plus custom tags;
- context: free text;
- contact details or social media handle: explicit opt-in, behind a plain-language warning that anyone who obtains this information could use it against them.

If the stated time or stated location is left empty, the form warns plainly that no lawyer will be able to find the clip until both are given (see §13 on how long such clips are kept).

Tags are stored as stable, language-neutral keys and shown by their **tag name** in the user's language (French and English for now). Default tags describe what can be seen, not conclusions, and their keys stay country-neutral so the list can travel; the French tag names use the French terms.

| Key | English | French |
|---|---|---|
| `arrest` | Arrest | Interpellation |
| `use_of_force` | Use of force | Recours à la force |
| `officer_id_legible` | Officer ID number legible | RIO lisible |
| `officer_id_not_visible` | Officer ID number not visible | RIO non visible |
| `police_armband_visible` | Police armband visible | Brassard police visible |
| `no_police_armband_visible` | No police armband visible | Pas de brassard police visible |
| `plainclothes_officers` | Plainclothes officers | Police en civil |
| `minor_in_frame` | Minor in frame | Personne mineure à l'image |
| `injury_visible` | Injury visible | Blessure visible |
| `tear_gas` | Tear gas | Gaz lacrymogène |
| `kettling` | Kettling (crowd encircled and held by police) | Nasse |

Custom tags are stored as typed, without translation.

After upload, show the upload code once on a **code card** designed to be screenshotted, with a clear "save this now, it cannot be recovered" message. The card shows the code and a **card label** the uploader types, pre-filled with a suggestion they can edit (e.g. built from the stated date and tags). The card label appears only on the card and is never stored on the server. A plain-language warning says that anyone who finds the card can read the clip's messages and delete it.

Limits: cap by file size, not duration (default 2 GB, configurable). A file over the cap is refused whole, never cut: the form states the cap, the browser checks the file's size before sending it, and the server enforces it on the bytes it receives. Rate-limit uploads; IPs may be used transiently for this (in cache, short TTL) but are never written to the database or logs. Limits are per connection and generous, since many phones at a protest can share one IP address (defaults: 20 uploads and 20 wrong codes per hour).

## 9. Ingest pipeline (background job)

1. The original is already in quarantine (§8), an area that is never served over the web.
2. Validate again with ffprobe, fully, that it is a real video (the first check ran at upload, §8); reject it otherwise.
3. Compute SHA-256 of the file exactly as received.
4. Compute per-stream hashes of the encoded packets (ffmpeg stream copy, no decoding).
5. Extract metadata (ffprobe; exiftool optional), confirming the quick read made at upload (§8). The metadata time goes to the index as found. GPS goes to the index as the metadata location, snapped to a cell of the size chosen at upload (§8); the precise value is never stored. Device make and model, software versions and unique identifiers are discarded.
6. Remux: copy the encoded video and audio streams into a new container of the same family (QuickTime or MP4 stays QuickTime or MP4, Matroska or WebM becomes Matroska) without decoding or re-encoding them, keeping only those streams (data tracks and cover art are dropped) and none of the container or stream metadata. This is the **evidence copy**. It is never re-encoded. Information the player needs that lives inside the streams (such as rotation) survives; times, locations and tags are held on the clip (§6), not in the file.
7. Recompute per-stream hashes on the evidence copy. They must equal step 4, or the job fails loudly.
8. Generate a low-res H.264 proxy for previews. The proxy is never evidence.
9. Delete the original: once step 7 passes, it is the only copy still carrying identifying metadata. Keep the hash record (results of steps 3, 4 and 7, plus the SHA-256 of the evidence copy so a lawyer can check a download, with timestamps kept to their 4-hour block, §6). It proves the frames are untouched, and lets a copy of the original (for example, still on the filmer's phone) be matched against what we hold.

If a job fails or exceeds its time limit (default 10 minutes per attempt, configurable), it is retried with backoff: five retries, after 1, 2, 4, 8 and 16 minutes. A file that is not a video, or no longer shows what the quick read at upload found, fails at once without retries. After the last failure, the original is deleted immediately, along with the clip's context and contact details, and the clip shows a failed status, so a code holder who returns with their code knows to upload again (with a new code). Clips still waiting for ingest are never deleted for age; a backed-up queue alerts the admin instead.

Later: a trusted timestamp (RFC 3161) over the hash record.

The queue counts as backed up when more than 20 clips are waiting, or when a clip is still waiting 2 hours after the end of its upload time's 4-hour block (both configurable): with all its retries, ingest takes at most about 1.5 hours, and the check needs no time more precise than the one stored (§6); the alert's own time is kept only to its 4-hour block too. The alert is shown once until the admin has seen it.

## 10. Security requirements

- Django's auth with the Argon2 password hasher. No hand-written auth or crypto.
- Encryption only through PyNaCl (libsodium). M5 starts with a written design that is reviewed before any code is written. It must cover key management (how a newly admitted lawyer gets access to existing clips, revocation, recovery); how code holders, who have no keys, can still read messages addressed to them and their own context and contact details; whether case labels, case areas and audit details can be encrypted without breaking matching and alerts (§3); and whether lawyers' two-factor keys and recovery codes, readable in the database today, should be encrypted.
- 2FA for applicants, lawyers and the admin through django-otp (ADR 0005): codes from an authenticator app, and ten single-use recovery codes shown once at setup. No SMS or email codes, and no page that turns it off. The QR code for setup is drawn on the server (segno). Wrong codes are slowed down by django-otp; many of them after a right password suggest a stolen password, and count towards the alert for many failed logins for one account.
- Failed logins are rate-limited per connection and per account on one connection (defaults: 20 and 10 per hour, configurable): over a limit, no password is checked, not even a right one, until the window ends. Limiting per account from anywhere would let anyone lock a lawyer out, so many failed logins or wrong second-factor codes for one account across connections (default 30 per hour) raise an alert to the admin instead. As with uploads, only keyed hashes of the IP address and the email address are kept, in the cache.
- A login lasts at most 24 hours (configurable).
- Django's built-in admin site is not installed: it can show and change any database table, which conflicts with an admin who manages accounts, not content (§5). The admin works only through the archive's own pages, each allowing one action.
- Uploaded files are untrusted: validate them, never execute them, never serve them from quarantine. Only video is accepted.
- No IP addresses, user agents or fields supplied by uploaders or code holders in application logs. (The audit log is not an application log; its entries hold keys and identifiers, not such text.)
- The audit log is append-only: the application has no update or delete path for it.
- Secrets come from environment variables; `.env` is gitignored.

## 11. Stack

- Python 3.12 in a pyenv virtualenv (`footage-archive`, selected via `.python-version`), Django, server-rendered templates, minimal JavaScript. Function over looks.
- Django's built-in translation framework (its tools need GNU gettext, from apt): French by default, English available. Every user-facing string is translatable from day one.
- SQLite in development, Postgres later.
- ffmpeg and ffprobe from apt. Whether package installs (apt and pip) are done by hand or by the agent is set per machine, in that machine's Claude Code instructions (`~/.claude/CLAUDE.machine.md`).
- Background tasks with Huey, its queue in a SQLite file of its own: no broker to run, retries with backoff and scheduled tasks built in. Tasks carry only identifiers. The worker runs as `python manage.py run_huey`.
- Email through Django's email framework: in development each email is printed in the terminal of the process that sends it, and nothing is sent; a mail provider, configured from environment variables, comes with deployment. Emails (invitations now, notifications in M6) never carry footage, anything about a clip, or vault contents.
- File storage behind Django's storage API: local disk now, S3-compatible object storage later.
- pytest with pytest-django. Test videos are generated with ffmpeg (synthetic `lavfi` sources with injected fake GPS and device tags), so tests never need real footage.
- Development on Ubuntu (WSL2 or a remote server) with pyenv, the repo in the Linux filesystem.

## 12. Milestones

Each milestone: plan first, then small steps, with tests green before moving on.

**M0: Skeleton.** Django project, settings from environment variables, Argon2 as the password hasher, translation set up (French default, English available), `.gitignore` (covering `.env`, `media_test/` and local media storage), pytest wired up, ffmpeg availability check, and a README with setup-from-scratch steps (pyenv included) that opens with a notice: learning build, not deployed, not for real footage.
*Done when* a fresh clone plus the README steps gives a running server and a passing `pytest`, a test confirms new passwords are hashed with Argon2, and the README's first lines carry that notice.

**M1: Anonymous upload.** Two-step upload form (§8) with the default tags in French and English, quarantine storage, the quick metadata read that pre-fills time and location, size cap, code card, upload code issued once and stored as a keyed hash, return-with-code to edit fields (with as-uploaded values and edit records) or delete the clip, deletion of originals whose form was never confirmed.
*Done when* tests cover code issuance, wrong-code rejection, editing (including that the metadata time cannot be changed and that old context and contact details are not kept), deletion, oversize rejection, and deletion of originals whose form was never confirmed, and the form renders in both languages.

**M2: Ingest pipeline.** Steps 1–9 of §9.
*Done when* tests show that (a) per-stream hashes match before and after stripping, (b) the evidence copy contains only video and audio streams and no GPS or device metadata, (c) a portrait video still displays upright, (d) a non-video file is rejected, (e) the proxy exists and the original is gone, (f) after a job's last failed retry, its original is deleted and the clip shows a failed status, and (g) no stored location is finer than the grid.

**M3: Roles.** Lawyer invitation and admission flow (§5), admin approval, declining applicants, suspension, reinstatement and revocation, mandatory 2FA (with recovery codes), audit log model (with the admin's account actions, §7), a minimal case (its lawyer, case label and whether it is open or closed: just enough for revocation to close it), and two alerts: a backed-up ingest queue (§9) and many failed logins for one account (§10).
*Done when* tests cover each role's permissions, including that an applicant, a declined applicant, a suspended lawyer or a revoked lawyer sees nothing and that a reinstated lawyer sees again; that no lawyer or admin page opens without a verified second factor; that an invitation link works only once, expires, and stops working once a new one is sent; that declining erases an applicant's name, bar and email address, and that a declined applicant's or revoked lawyer's address can be invited again as a new applicant while the old account stays as it was; that each of the admin's actions on an account writes an audit entry; that revocation closes all of a lawyer's cases and cannot be undone; and that a backed-up ingest queue and many failed logins for one account each raise an alert.

**M4: Case-scoped search.** Cases in full, building on M3's minimal case: time window and area, size caps, admin approval of larger cases, closing by the lawyer and automatic closing; matching; proxy previews with metadata vs stated values, as-uploaded values, edits and the time-mismatch highlight; downloads with hash record; audit logging of lawyer actions; unusual-activity alerts.
*Done when* tests show that a lawyer only ever sees clips that match their own open cases, that a case over the caps cannot be created without approval, and that every lawyer action writes an audit entry.

**M4.5: Map for case areas.** A map on which a lawyer picks a case's area as grid cells, as an alternative to typing a place and a radius (which stays, also for keyboard and screen-reader use). Tiles, map style and fonts are served by the archive itself, to logged-in lawyers only, never fetched from a third party. Cells are worked out in the browser, and only cell ids reach the server, which checks them and measures the area against the size caps as it does now. Dependencies (a map library, a tile format and the tiles' coverage) are chosen with the user before planning.
*Done when* tests show that a case can be created from cells picked on the map, that the server accepts only finest-grid cell ids and applies the same size caps, and that the map page loads nothing from outside the archive.

**M5: Encryption.** Design document first (see §10 for what it must cover), reviewed, then implementation behind the vault interface.

**M6: Upload robustness, messaging and lawyer collaboration.** Resumable uploads (tus protocol); messages between lawyers and code holders (with the lawyer identification setting and message expiry); email notifications for lawyers (at first only of a new message), each turned on or off on a settings page and saying only that something is waiting, never the message or anything about the clip; reports (wrong time or place, doubt marker, content warning and withdrawal); lawyer tags with tag source, three-lawyer removal and the admin's tag history.

**M7: Licence.** Choose a licence for the code (e.g. MIT, Apache-2.0 or AGPL-3.0) and for the documentation, add it to the repository and name it in the README. Until then the code is all rights reserved. This milestone comes before accepting any outside contribution, whatever milestone we are on.
*Done when* the repository has a LICENSE file and the README names the licence.

**Later.** Passkeys (WebAuthn) as a second factor that a phishing site cannot relay (ADR 0005); a database-level guard on the audit log, with the move to Postgres: the application's database role may only read and add to its table (triggers on SQLite would vanish whenever a migration rebuilds the table); optional uploader accounts; a partner password-manager-type service where code holders can keep their upload codes (one-time setup, password-protected; check how far that protects against compelled disclosure under French law); attachments to a clip, allowlisted types only (notably high-resolution photos in which an officer's RIO number is legible); trusted timestamps; verifiable excerpt export (per-packet hashes so an excerpt can be checked against the original); OCR of RIO numbers, lawyer-side only (within the §4 exception); periodic collusion detection over the audit log (lawyers who repeatedly agree on the same tag removals or clip withdrawals), imperfect but a check on abuse of the count-based rules; watch the audit log's size and archive or compact it if it grows (it records human actions, so growth should be slow); native app; deployment (passing `manage.py check --deploy`); external security review.

## 13. Open questions

- Keep optional uploader accounts at all, given that they link clips together? The same question covers letting a code holder attach a new clip to an existing upload code (for now: one code per clip, always). Linking has no collision risk, but it lets the server, and anyone who seizes it or the code, build a list of protests one person attended, and one leaked code then exposes every linked clip.
- Discard the untouched original after hashing (current default), or also keep it in the vault? Keeping it would keep precise GPS inside the file, which ADR 0002 rules out unless that decision is revisited.
- The hash record keeps the original's SHA-256 even after the clip is deleted, so that a copy of the original can be matched against what we held. The same match works against the filmer: if their phone and the server are both seized, it proves that the video on the phone was uploaded. For the legal review: is this worth keeping, and for how long after deletion?
- **Clips with no capture time or no location (important, revisit).** They can never match a case, so as they stand they are useless. We hold them for a while so the code holder can come back and add the missing time or place, but holding unfindable footage indefinitely conflicts with data minimisation. To decide: how long they are held, whether the code holder is warned, and what happens when the period ends.
- **Failed clips.** When ingest fails for good (§9), the original is deleted, and so are the context and contact details; no evidence copy, proxy or hash record was ever kept. The clip itself stays, with its times, locations, tags, as-uploaded values and edits, so that a code holder who comes back with the code learns that ingest failed and knows to upload again; with the code they can also delete what is left. As it stands, nothing else deletes it. To decide, alongside the question above: how long a failed clip is kept, and what happens when that period ends.
- Files over the size cap (§8): what can a filmer with a bigger video do? Trimming it on the phone often re-encodes it and rewrites its metadata time. Resumable uploads (M6) are where a higher cap becomes practical.
- Choosing the stated location on a map or by address (§8). Map tiles or an address lookup from a public server would tell that server, with the uploader's IP, roughly where they filmed; self-hosted tiles are one answer. Until then the stated location is entered as coordinates, pre-filled from the file's GPS.
- Error reports: Django's error reports show local variables, which would include form data, precise GPS and upload codes. Nothing sends them today (no `ADMINS`, no error tracker); before adding either, decide how to keep these out.
- **Account recovery.** M3 handles neither a lost password nor a lost second factor (beyond recovery codes). Django's password reset by email is the usual answer for a password. For a second factor, a new invitation and help from the admin are both possible routes, but neither may let the admin take over a lawyer's account, and with it the lawyer's cases. Once M5 gives lawyers keys, recovery must cover those too (§10).
- **Applicants left waiting.** An applicant the admin neither admits nor declines stays an applicant indefinitely, with their name, bar and email address. Should applicants be declined automatically after a period, and how long?
- Long term: stay standalone, or move to (or build on) GlobaLeaks for international use? Language-neutral tag keys keep that door open.
- Before any real use: hosting-provider data retention rules versus anonymity, hosting-provider duties on illegal content (removal once aware, reporting e.g. to PHAROS) given that the admin cannot view clips, GDPR legal basis and impact assessment (AIPD), who operates the service (an association?), and which defence organisations could vouch for lawyers.
