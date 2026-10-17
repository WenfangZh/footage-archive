# CLAUDE.md

Learning build of a footage archive for defence lawyers. Three documents define it:
- **SPEC.md** is the source of truth for what we're building and why; read the relevant sections before planning any milestone.
- **GLOSSARY.md** defines what the archive's concepts are called.
- **docs/adr/** records decisions that are hard to reverse; don't go against one without discussing it first.

## Workflow

- One milestone at a time (SPEC.md §12). Before starting one, propose the steps and wait for my OK.
- Small steps. After each one: run the tests, summarise what changed, propose a commit message.
- Before committing: check the author identity, stage files by name (never `git add .`), and check the list of staged files. One commit per change.
- Every change goes through a pull request. Branch off an up-to-date master, named `<area>/<topic>` (e.g. `ci/tests`, `m2/stream-hashes`); never commit to master (branch protection blocks it anyway).
- Keep each PR small enough to review in one sitting. PRs are squash-merged, so the PR title becomes the commit subject on master.
- `git push` and `gh pr create` need my OK each time. The PR description opens with the **Security-relevant:** line, then `## Why`, `## What changed`, `## Scope` (including what's left out) and `## Verification`.
- The `tests` CI check must pass; I review and merge on GitHub. After the merge, switch to master and pull. You may delete the local branch once its diff against master is empty.
- A milestone's PRs usually form a stack: each branch starts from the previous one, and each PR is based on the branch before it. They are merged in order. After each merge, retarget the next PR to master before deleting the merged branch (deleting it first closes the next PR), rebase the rest of the stack with `git rebase --onto master <merged branch's old tip> <top branch> --update-refs`, and push the next branch with `--force-with-lease`. `gh pr edit --base` is broken; use `gh api -X PATCH repos/WenfangZh/footage-archive/pulls/<N> -f base=master`.

## Subagents

- One main session builds each milestone and talks to me. Subagents never make decisions that are mine: they report options and findings to the main session, which brings them to me.
- **Review:** before each PR is pushed, a subagent with no prior context reviews the branch's diff for correctness and security, against SPEC.md, the ADRs and the hard rules below. The main session fixes or answers each finding, and the PR's `## Verification` says what the review found.
- **Research ahead:** one subagent may prepare what the next decisions need, such as options for a dependency, or a design draft for me to review. It doesn't change the repository; it writes to the scratchpad and reports back.
- Work that is truly independent of the milestone in progress may run in a separate worktree. Building milestones in parallel is out: they depend on each other, and on my decisions.
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
- Background worker (ingest, and scheduled deletion of unconfirmed originals): `python manage.py run_huey`
- Delete unconfirmed originals by hand: `python manage.py delete_unconfirmed_originals`
- Open a pull request: `gh pr create --base master` (CI: `.github/workflows/tests.yml`)
