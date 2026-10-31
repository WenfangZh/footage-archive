# CLAUDE.md

Learning build of a footage archive for defence lawyers. Three documents define it:
- **SPEC.md** is the source of truth for what we're building and why; read the relevant sections before planning any milestone.
- **GLOSSARY.md** defines what the archive's concepts are called.
- **docs/adr/** records decisions that are hard to reverse; don't go against one without discussing it first.

## Workflow

- One milestone at a time (SPEC.md §12). Before starting one, propose the steps and wait for my OK.
- Small steps. After each one: run the tests, summarise what changed, propose a commit message.
- Tests cover the milestone's "Done when" list and the hard rules below, not every branch of the code.
- Before committing: check the author identity, stage files by name (never `git add .`), and check the list of staged files. One commit per change.
- Every change goes through a pull request. Branch off an up-to-date master, named `<area>/<topic>` (e.g. `ci/tests`, `m2/stream-hashes`); never commit to master (branch protection blocks it anyway).
- Keep each PR small enough to review in one sitting. PRs are squash-merged, so the PR title becomes the commit subject on master.
- Stacks are at most three PRs deep. A PR that doesn't depend on another branches off master. Build and open the PRs reserved to me (below) first in a milestone, so I can merge them while the rest goes on, and keep other PRs from depending on them where possible.
- Pushing this repository's feature branches (with `--force-with-lease` after rebasing a stack; never plain `--force`, never master), rebasing them, and the `gh` calls for its pull requests (opening, checking, editing the description, retargeting, deleting merged branches, and merging, except the PRs reserved to me below) need no OK. The PR description opens with the **Security-relevant:** line, then `## Why`, `## What changed`, `## Scope` (including what's left out) and `## Verification`. It is a short briefing for someone who has the diff, with no file-by-file list.
- The `tests` CI check must pass. Then:
  - The main session squash-merges a PR itself once the check is green and every finding of its review (below) is fixed or answered in the PR's `## Verification`. Its summary lists each PR it merged; I may review any of them afterwards.
  - Reserved to me: a PR whose diff changes encryption (PyNaCl, `clips/vault.py`), login or 2FA (`accounts/two_factor.py`, the login views and templates, the auth settings), who may do what (`accounts/roles.py`, `accounts/access.py`), the audit log (`audit/models.py`, `audit/migrations/`, anything enforcing append-only), CI (`.github/`), an ADR, or this file. When in doubt, it is mine. The main session still pushes and opens it; only the merge is mine. A reserved PR holds back the PRs stacked above it until I merge it, except in a stack merged top-down (below).
  - After a merge, switch to master and pull, then delete the merged branch on GitHub, and locally once its diff against master is empty (in a stack merged bottom-up, only once the next PR is retargeted; in a stack merged top-down, all its branches only once the bottom PR is in master).
- PRs that depend on each other form a stack: each branch starts from the previous one, and each PR is based on the branch before it.
  - A stack whose PRs are all reserved to me is merged top-down, and lands on master as one commit. When handing it over, the main session says so and lists its PRs from the top. I merge the top PR into the branch below it, and so on down, until the whole stack is in the bottom PR, then tell the main session; until then it pushes nothing to the stack's branches. It then resets its local bottom branch to the one on GitHub and checks that it matches the old top branch, updates the bottom PR's title and description to cover the whole stack, rebases it onto master if master has moved (again if master moves before my merge), and tells me once its CI passes. I merge it into master. Nothing is retargeted. If a PR is merged out of order, I stop and tell the main session, which retargets what is left and carries on bottom-up.
  - Every other stack is merged bottom-up, in order. Before a PR in it is merged, whoever merges it checks that its base reads master; when handing over a reserved one, the main session says so. Deleting a PR's base branch closes the PR, except when I delete a merged PR's branch from its page on github.com: GitHub then retargets the next PR to master itself (check that it did). When the main session deletes a merged branch, it retargets the next PR first. If the next PR was closed, restore the deleted branch from the merged PR's page, reopen the PR, then retarget it. Then rebase the rest of the stack with `git rebase --onto master <merged branch's old tip> <top branch> --update-refs`, and push the next branch with `--force-with-lease`. `gh pr edit --base` is broken; use `gh api -X PATCH repos/WenfangZh/footage-archive/pulls/<N> -f base=master`.

## Subagents

- One main session builds each milestone and talks to me. Subagents never make decisions that are mine: they report options and findings to the main session, which brings them to me.
- **Review:** one review per PR. Before it is pushed, a subagent with no prior context reviews the branch's diff for correctness and security, against SPEC.md, the ADRs and the hard rules below. The main session fixes or answers each finding, and the PR's `## Verification` says what the review found. If a fix answers a blocker, a fresh subagent checks that fix commit only (at the same tier), and `## Verification` says so; there is no second review of the whole diff. A finding that only asks for more test coverage, beyond the "Done when" list and the hard rules, goes to a follow-up list under `## Scope`, not into the PR.
- **Research ahead:** one subagent may prepare what the next decisions need, such as options for a dependency, or a design draft for me to review. It doesn't change the repository; it writes to the scratchpad and reports back.
- **Models:** the research agent runs on Sonnet at medium effort. Each review uses the highest tier its diff touches: Opus for encryption (PyNaCl, the vault's encryption, and M5's design document); Sonnet at high effort for all other code, which includes templates, translations, tests, migrations and configuration, and for prose docs that record a decision (in SPEC.md, GLOSSARY.md, an ADR or this file); Haiku for prose docs that record none.
- Work that is truly independent of the milestone in progress may run in a separate worktree. Building milestones in parallel is out: they depend on each other, and on my decisions.
- Ask before adding a dependency, plugin, skill or MCP server: what it does, why we need it, and what the alternative is.
- If SPEC.md is ambiguous or seems wrong, ask instead of guessing. When we decide something new, propose the matching edit to SPEC.md, update GLOSSARY.md when a term is settled, and propose an ADR for a decision that is hard to reverse, surprising without context, and the result of a real trade-off.
- Start every summary with a **Security-relevant:** line covering auth, permissions, crypto, file handling or logging of personal data, or "none".

## Working principles

Taken from the principles of pstack's poteto-mode, without its process (no delegated coding, design arenas or verification swarms). The rest of this file wins where they differ.

- **Data shape first.** Before writing logic, name the data it works on and the structure that holds it (a model, a choices field, a lookup table), chosen so a wrong state can't be stored. One more branch in an if/else chain, or a second flag that must agree with the first, means the shape is wrong. Ask what happens if a second request or the worker changes the same rows at once.
- **Smallest change.** Remove dead code in the files you're changing before building on them, in its own commit. No speculative options, guards or layers; inline a wrapper with one caller. No Python code outside this repository calls its functions, so a changed function takes all its callers along in the same PR, with no compatibility path. What lives outside the code keeps working: stored rows (through migrations), the audit log, URLs, issued upload codes and queued tasks.
- **Validate at the boundary.** Untrusted input (form fields, uploaded files, HTTP headers, environment variables) is checked once, where it enters, and turned into checked values; the code behind trusts them. A view's role and case checks are a boundary too, on every view. This never removes a hard rule's check or an access check where data is read or served (a case-scoped query, a role check in a helper): those are defence in depth, not redundant validation.
- **Tests assert behaviour.** Drive the code as its users do (a request, a command, a model's public method) with concrete input, and derive the expected value from SPEC.md, never from a run of the code. A test should fail when the behaviour breaks and survive a refactor.
- **Prove it on the real thing.** Passing tests are not the whole proof: run the change as a person would (the dev server, `curl`, a browser) and say in `## Verification` what was run and what it showed. Label each claim in a summary as checked, inferred or a guess, and don't hand me a check you could run (what this file or my global instructions reserve to me stays mine).
- **Fix the root cause.** Reproduce a bug first, trace it to the first wrong decision, and fix it there and wherever the same pattern appears. A guard is a fix only for a state that may legitimately happen. After two failed fixes resting on one assumption, write the assumption down and test it before a third.
- **Re-runnable operations.** A command, task or script reaches the same end state when run twice or after a crash halfway: write to a temporary file and rename it into place, and compare what exists with the goal. Audit entries are the exception: each run that acts is logged.
- **Scripts over hand steps.** Repeatable work (fetching data, generating files) is a management command or a script in the repository, which a reviewer can rerun. When the same correction comes up twice, prefer a test or system check to another line of instructions.
- **Observe before asking.** Don't ask me what running something would settle (behaviour, size, output): run it and report.
- **When I'm away.** If I hand over a run and say not to wait, decide each call that is mine with the option you'd recommend, log it in the scratchpad as you go, and report each one with why and its alternatives, as if presenting the choice. Merges reserved to me, new dependencies, and everything else this file or my global instructions say waits for me still wait.
- **Comments** say why, where the code can't.
- **Keep going.** Opening a PR doesn't pause the work: carry on with the approved steps while CI runs.

## Hard rules

- This repository is public. Treat every commit as permanent: no secrets, and no personal data about real people (names, emails, phone numbers, locations) in code, fixtures or commit messages.
- Never write custom cryptography, password hashing or authentication. Use Django's auth (Argon2 hasher), and libsodium: PyNaCl on the server, libsodium.js in the browser.
- The evidence copy is never re-encoded. Only proxies are.
- Uploaded files are untrusted: only video is accepted, validated with ffprobe, never executed, never served from quarantine.
- No public views of footage, ever. Footage access is lawyer-only, case-scoped and logged.
- The audit log is append-only: no code path updates or deletes its entries.
- Never persist IP addresses, user agents or fields supplied by uploaders or code holders in logs. The audit log holds keys and identifiers, not such text.
- No location is stored more precisely than the grid; precise GPS is discarded at ingest (ADR 0002).
- Upload codes are stored only as a keyed hash, and each covers exactly one clip (ADR 0001). The one other value stored that comes from a code is its clip's code holder public key (ADR 0001, addendum of 2026-10-25). The card label is never stored on the server.
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
- Map data (tiles and fonts into `var/map/`, needs the `pmtiles` tool on `PATH`): `python manage.py fetch_map_data`
- Open a pull request: `gh pr create --base master` (CI: `.github/workflows/tests.yml`)
