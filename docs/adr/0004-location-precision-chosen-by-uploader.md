# Stored locations are no more precise than the uploader chose

A clip's stated location is stored as the one H3 cell that contains the given point, at a resolution set by its stated precision: exact spot 11 (about 50 m across), street 10 (about 130 m), neighbourhood 9 (about 340 m). When the file has GPS, the uploader also chooses how precisely it is kept as the metadata location, from resolution 11 to 9, with 11 pre-selected; the code holder can later make it coarser, never finer, since the precise position is discarded at ingest (ADR 0002). Storing a vague stated location as a fine cell would keep more than the uploader chose to give, and since the point is often pre-filled from the file's GPS, it would quietly keep the GPS position. The two precisions are chosen separately because they answer different questions: how precisely the uploader knows where they filmed, and how much of the file's GPS position they are willing to leave with us.

## Considered Options

- **A ring of fine cells around the point**: covers the same area, but its centre is the point, so it would give the point away.
- **The metadata location kept automatically at the stated precision**: it would tie how much GPS we keep to how well the uploader knows the place, which are different questions.
- **A place name stored alongside the cells**: a free-text place field invites addresses far more precise than the grid, matching needs cells anyway, and the context field already takes free text.
- **Pre-selecting the coarsest metadata precision**: more protective by default, but most people keep what a form pre-selects, so most clips would carry only a rough check against the stated location.

## Consequences

- The point may sit near the edge of its cell, so a cell is not centred on where the clip was filmed; the matching padding (SPEC §7) absorbs this.
- No cell is ever finer than the grid's limit (ADR 0003), whatever the precision.
- Before real use, the legal review (SPEC §13) should check the pre-selected finest precision against GDPR's data protection by default (article 25(2)).

## Addendum (2026-10-18): padding is now the margin

The matching padding named under Consequences is now each case's margin (SPEC §7, GLOSSARY). A clip whose cell lies only in the margin still matches, as a margin match, listed after the core matches.

## Addendum (2026-11-01): the stated location is now painted cells

From M6.6, the stated location is a set of the grid's finest cells that the uploader paints on the map (ADR 0009); the painted area is its own precision. The three precisions remain for coordinates typed without JavaScript, which now stand for the finest cells of the hexagon they name, not for the hexagon itself: the same area, centred on the hexagon, not on the point, so the reason given above against a ring of fine cells still holds. Existing clips were converted the same way. The metadata location's precision, and the rule that it can only be made coarser, are unchanged.
