# The code is under the AGPL, the documentation under CC BY, and contributions come in on the same terms

The archive's code is licensed under the GNU Affero General Public License, version 3 or any later version (AGPL-3.0-or-later). Its documentation, the Markdown files (README.md, SPEC.md, GLOSSARY.md, CLAUDE.md, CONTRIBUTING.md, THIRD_PARTY_NOTICES.md, docs/ with the ADRs, and the READMEs in other folders), is under Creative Commons Attribution 4.0 (CC BY 4.0). Everything else of ours in the repository, translations, tests and templates included, counts as code; third-party files keep their own licences. A contribution is licensed under the licence of the files it changes, as GitHub's terms of service already provide (section D.6); there is no contributor licence agreement and no sign-off, and contributors may use a pseudonym.

The archive is a service: lawyers and uploaders use it over the network, so a licence that only covers copies handed out (MIT, Apache-2.0, even the plain GPL) would let anyone run a changed copy, with a weakened privacy rule or analytics added, without its users ever seeing the change. The AGPL makes whoever runs a changed copy offer its source to those users (section 13), so the people a weaker copy would harm, lawyers and uploaders, can find out. That is why the archive has a licences page, linked from every page, with a link to the source of the code it runs, set by `SOURCE_URL`.

## Considered Options

- **MIT**: the simplest to reuse, but a closed copy may run changed code with nothing to show; no explicit patent grant.
- **Apache-2.0**: permissive like MIT, with an explicit patent grant; closed copies are allowed just the same.
- **EUPL-1.2**: drafted for EU law and equally valid in French, and it covers use over a network; but a combined work may be relicensed under a "compatible licence" such as the MPL-2.0, which has no network clause, and fewer developers know it.
- **AGPL-3.0-only** instead of "or later": a future version of the AGPL could never apply without every contributor's consent, which without a contributor licence agreement is out of reach.
- **For the documentation, CC BY-SA 4.0**: adapted copies would stay open, but a defence organisation copying the threat model into its own impact assessment or policy would have to publish that under CC BY-SA too, and Creative Commons declared CC BY-SA 4.0 compatible one way, into GPLv3 works, with nothing said of the AGPL, so text moved from the documentation into the code would, in our reading, stand on uncertain ground. Ideas are not protected by copyright anyway; the protection that matters is the code's.
- **For contributions, a sign-off (DCO) or a contributor licence agreement (CLA)**: a sign-off puts a name and an email address in every commit, against this repository's rule on personal data and a risk for people contributing to a tool used against police misconduct; an agreement would let one holder relicense the code later, deters contributors and ties contributions to identities.

## Consequences

- Every dependency is compatible: they are under MIT-style, BSD, ISC and Apache-2.0 licences or the Unlicense, all of which may be combined into AGPL work (THIRD_PARTY_NOTICES.md). A future dependency under the GPLv2 only would not be.
- Each version published stays under the licence it was published with. Without a contributor licence agreement, changing the licence later needs every contributor's consent, except moving to a later AGPL version.
- Running the archive unchanged needs nothing but the licence; a changed copy must set `SOURCE_URL` to where its own source can be had.
- Some organisations do not use AGPL code. Some of the archive's likely reusers do: SecureDrop and GlobaLeaks (ADR 0007, SPEC §13) are under the AGPL too.
- No licence stops an operator who lies about the code they run. The AGPL deters careless or commercial copies; trust in a running archive comes from who operates it and from external review (SPEC §13).
- Whether code written largely with an AI assistant is protected by copyright at all, and so how far the licence can be enforced, is a question for the legal review (SPEC §13).
