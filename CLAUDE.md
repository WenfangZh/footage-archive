# CLAUDE.md

Learning build of a footage archive for defence lawyers. Three documents define it:
- **SPEC.md** is the source of truth for what we're building and why; read the relevant sections before planning any milestone.
- **GLOSSARY.md** defines what the archive's concepts are called.
- **docs/adr/** records decisions that are hard to reverse; don't go against one without discussing it first.

## Workflow

- One milestone at a time (SPEC.md §12). Before starting one, propose the steps and wait for my OK.
- Small steps. After each one: run the tests, summarise what changed, propose a commit message.
- Before committing: check the author identity, stage files by name (never `git add .`), and check the list of staged files. One commit per change.
- Ask before adding a dependency, plugin, skill or MCP server: what it does, why we need it, and what the alternative is.
- If SPEC.md is ambiguous or seems wrong, ask instead of guessing. When we decide something new, propose the matching edit to SPEC.md, update GLOSSARY.md when a term is settled, and propose an ADR for a decision that is hard to reverse, surprising without context, and the result of a real trade-off.
- Start every summary with a **Security-relevant:** line covering auth, permissions, crypto, file handling or logging of personal data, or "none".

## Hard rules

- This repository is public. Treat every commit as permanent: no secrets, and no personal data about real people (names, emails, phone numbers, locations) in code, fixtures or commit messages.
- Never write custom cryptography, password hashing or authentication. Use Django's auth (Argon2 hasher) and PyNaCl.
- The evidence copy is never re-encoded. Only proxies are.
- Uploaded files are untrusted: only video is accepted, validated with ffprobe, never executed, never served from quarantine.
- No public views of footage, ever. Footage access is lawyer-only, case-scoped and logged.
- The audit log is append-only: no code path updates or deletes its entries.
- Never persist IP addresses, user agents or fields supplied by uploaders or code holders in logs. The audit log holds keys and identifiers, not such text.
- No location is stored more precisely than the grid; precise GPS is discarded at ingest (ADR 0002).
- Upload codes are stored only as a keyed hash, and each covers exactly one clip (ADR 0001). The card label is never stored on the server.
- The metadata time is never modified.
- No face recognition or other automated identification of people. The only exception: identifiers that can only belong to police or other public officials acting as such (an officer's RIO number, a police armband) may be read automatically, for lawyers only. Anything a civilian could also carry or wear is never identified automatically (SPEC.md §4).
- Configuration comes from environment variables; `.env` is gitignored.
- No video files in git. Manual test media lives in `media_test/` (gitignored); automated tests generate small synthetic videos with ffmpeg.

## Conventions

- Every user-facing string goes through Django's translation functions (French default, English available). Tags are stored as language-neutral keys with translated tag names (SPEC.md §8).
- GLOSSARY.md defines the design language of the archive: what its concepts are called in models, fields, UI text, docs and discussion (a `Clip` model, never `Video`). Programming terms used in their technical sense are exempt from its _Avoid_ lists: Django's `User` model, an HTTP file upload, a log handler. Where such a term could be mistaken for a design term, make the technical sense explicit (e.g. a short comment, or a name like `auth_user`).
- The _Avoid_ lists govern English. French UI text uses natural French, with the one fixed French term GLOSSARY.md gives for each concept; add the French term there when it is first needed, and flag any case where two concepts would share a French word. For people, use gender-neutral phrasing when it stays short ("personne qui…"), otherwise the inclusive point (avocat·e).

## Environment

- Python: pyenv virtualenv `footage-archive` (Python 3.12), selected automatically in this folder via `.python-version`. Use `python` directly; don't create a `.venv`.
- ffmpeg, ffprobe and gettext from apt.

## Commands

Fill these in as they're created:
- Tests: `pytest`
- Dev server: `python manage.py runserver`
- System checks: `python manage.py check`
- Extract translatable strings: `python manage.py makemessages -l fr --ignore=var --ignore=tests --ignore=media_test`, then translate in `locale/fr/LC_MESSAGES/django.po`
- Compile translations: `python manage.py compilemessages` (the `.mo` files are gitignored; a test fails if they are missing or stale)
