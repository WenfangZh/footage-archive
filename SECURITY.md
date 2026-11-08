# Security

This is a learning build (SPEC.md §2): it holds no real footage and no real users, and nothing is deployed. It is still built as if the stakes were real, so reports of vulnerabilities are welcome.

## Reporting a vulnerability

Please report it privately, through GitHub's private vulnerability reporting: on this repository's **Security** tab, choose **Report a vulnerability**. Don't open a public issue or pull request for it.

Say what you found, how to reproduce it (against your own local copy, with synthetic data), and what you think it lets someone do. Never test against a server you don't run yourself.

## What is checked

`scripts/security_scan.sh` looks for published vulnerabilities in the pinned packages, unsafe patterns in the code, and secrets anywhere in the git history (SPEC.md §12, M10). CI runs it on every pull request and push to master, and weekly, as the `security` check. The threat model is SPEC.md §3.
