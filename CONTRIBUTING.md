# Contributing

This is a learning build (README.md): it holds no real footage and has no real users. Issues are welcome, and so are pull requests, after an issue where the change is discussed.

## Licence of contributions

A contribution is licensed under the licence of the files it changes: the GNU Affero General Public License, version 3 or any later version, for code, and Creative Commons Attribution 4.0 for the documentation (README.md, Licence). GitHub's terms of service already say so (section D.6). There is no contributor licence agreement and no sign-off line: you keep the copyright in what you contribute.

You may contribute under a pseudonym. Commits are public and permanent, so leave out anything that identifies you that you don't want there, including your email address: GitHub can give you a `noreply` address for commits.

Contribute only what you have the right to: your own work, or code under a licence compatible with the AGPL, recorded in THIRD_PARTY_NOTICES.md with its notices.

## Security problems

Don't open a public issue for a security problem: use GitHub's private vulnerability reporting, under the repository's Security tab.

## How changes are made

SPEC.md says what the archive does and why; GLOSSARY.md what its concepts are called; docs/adr/ records decisions that are hard to reverse. CLAUDE.md holds the working rules, including the hard rules every change must keep: no secrets or personal data in the repository, no custom cryptography, no public views of footage, an append-only audit log, and the others listed there. Every user-facing string is translatable, in French and English. Each change goes through a pull request against `master`, with the tests passing (`pytest`).
