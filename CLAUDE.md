# CLAUDE.md

Learning build of a footage archive for defence lawyers. **SPEC.md is the source of truth** for what we're building and why; read the relevant sections before planning any milestone.

## Working with me

- I'm comfortable with Python from data science work, but new to Django and web development. When you introduce a pattern or tool I may not know, explain it in a sentence or two.
- One milestone at a time (SPEC.md §12). Before starting one, propose the steps and wait for my OK.
- Small steps. After each one: run the tests, summarise what changed, propose a commit message.
- Ask before adding a dependency, plugin, skill or MCP server: what it does, why we need it, and what the alternative is.
- If SPEC.md is ambiguous or seems wrong, ask instead of guessing. When we decide something new, propose the matching edit to SPEC.md.
- Start every summary with a **Security-relevant:** line covering auth, permissions, crypto, file handling or logging of personal data, or "none".

## Hard rules

- This repository is public. Treat every commit as permanent: no secrets, and no personal data about real people (names, emails, phone numbers, locations) in code, fixtures or commit messages.
- Never write custom cryptography, password hashing or authentication. Use Django's auth (Argon2 hasher) and PyNaCl.
- Evidence video files are never re-encoded. Only browsing proxies are.
- Uploaded files are untrusted: validate with ffprobe, never execute, never serve from quarantine.
- No public views of footage, ever. Footage access is lawyer-only, case-scoped and logged.
- Never persist IP addresses, user agents or uploader-supplied fields in logs.
- No face recognition or other automated identification of people.
- Never run Windows programs (anything ending in `.exe`), and never read or write under `/mnt/c`.
- Configuration comes from environment variables; `.env` is gitignored.
- No video files in git. Manual test media lives in `media_test/` (gitignored); automated tests generate small synthetic videos with ffmpeg.

## Conventions

- Every user-facing string goes through Django's translation functions (French default, English available). Tags are stored as language-neutral keys with translated labels (SPEC.md §8).

## Environment

- WSL2 on Windows 11, zsh with oh-my-zsh. The repo lives in the Linux filesystem, not under `/mnt/c`.
- Python: pyenv virtualenv `footage-archive` (Python 3.12), selected automatically in this folder via `.python-version`. Use `python` directly; don't create a `.venv`.
- ffmpeg, ffprobe and gettext from apt.
- Your shell commands run in Claude Code's sandbox in strict mode. Package installs (pip and apt), `git push` and the dev server are mine: give me the exact command and I'll run it in my own terminal.

## Commands

Fill these in as they're created:
- Tests: `pytest`
- Dev server (I run it): `python manage.py runserver`
