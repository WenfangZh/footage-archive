# Two-factor authentication with django-otp

Applicants, lawyers and the admin use django-otp for their mandatory second factor: codes from an authenticator app (TOTP) and ten single-use recovery codes, with our own small pages for setting it up and entering a code, and our role checks requiring a verified session (SPEC.md §10). It is small, plugs into Django's own login and never touches password hashing, stores no IP addresses, user agents or phone numbers, has no SMS, email or phone features that would need switching off, and throttles wrong codes itself. Switching packages later would mean every lawyer setting up their second factor again, which is why this is recorded.

## Considered Options

- **django-allauth's MFA**: very active, with passkeys and full French built in, but about 40,000 lines that take over every account page, with no setting for mandatory MFA and a re-authentication step a newly invited lawyer would hit at once.
- **django-two-factor-auth**: complete pages, but the organisation hosting it, Jazzband, announced on 14 March 2026 that it is winding down (https://jazzband.co/news/2026/03/14/sunsetting-jazzband), and it installs a phone-number package we would never use.

## Consequences

- TOTP keys and recovery codes are readable in the database, as with every option. M5's design must decide whether to encrypt them (SPEC.md §10).
- TOTP codes can be relayed by a phishing site in real time; passkeys (WebAuthn) cannot. django-otp's passkey add-on is young and has no French yet, so passkeys are left for later.
