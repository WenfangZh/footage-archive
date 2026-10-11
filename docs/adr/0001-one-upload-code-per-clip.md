# One upload code per clip, never linked

Each upload code covers exactly one clip, and codes cannot be linked to each other, even though one code for several clips would be more convenient for filmers. Linked clips let the server, and anyone who seizes it or a code, build a list of the protests one person attended, which can identify them without any name being stored; and one leaked code would expose, and allow deletion of, every linked clip. Code collisions were considered and are not a concern (128 random bits, unique lookup). Convenience is instead addressed by a screenshot-friendly code card with a card label the uploader chooses.

## Considered Options

- **Codes link clips by default** (a code works like an account): rejected for the aggregation risk above.
- **Opt-in linking with a warning**: deferred together with optional uploader accounts (SPEC §13), since it is the same question.
