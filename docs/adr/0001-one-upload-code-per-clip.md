# One upload code per clip, never linked

Each upload code covers exactly one clip, and codes cannot be linked to each other, even though one code for several clips would be more convenient for filmers. Linked clips let the server, and anyone who seizes it or a code, build a list of the protests one person attended, which can identify them without any name being stored; and one leaked code would expose, and allow deletion of, every linked clip. Code collisions were considered and are not a concern (128 random bits, unique lookup). Convenience is instead addressed by a screenshot-friendly code card with a card label the uploader chooses.

## Considered Options

- **Codes link clips by default** (a code works like an account): rejected for the aggregation risk above.
- **Opt-in linking with a warning**: deferred together with optional uploader accounts (SPEC §13), since it is the same question.

## Addendum (2026-10-17): codes have 140 random bits

Upload codes were built with 140 random bits, not 128: 28 characters in Crockford's base32, at 5 bits each (SPEC.md §5, `clips/upload_codes.py`). The spec asked for at least 128 bits when this ADR was written. Collisions are still not a concern.

## Addendum (2026-10-25): a code also gives its clip's code holder key

Since M5, the server derives a key pair from each upload code (BLAKE2b with the label `fa-codeholder-v1`) and stores its public half on the clip, so that code holders can read the clip's encrypted context, contact details and messages without an account (docs/m5-encryption-design.md §7.2). The keyed hash stays the only way a code is looked up. The public key is a second check of the code that needs no server secret: a copy of the database can test a guessed code against it, which 140 random bits make harmless. Each public key comes from one clip's code alone, so it links no clips together.
