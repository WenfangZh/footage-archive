# A clip's stated location is the set of cells its uploader paints

Until M6.6, a clip's stated location was one H3 cell, sized by a precision the uploader chose: exact spot, street or neighbourhood, about 50, 130 or 340 m across (ADR 0004). On the map of M6.5, those three sizes turned out to be hexagons that look like nothing on the ground: "street" draws no street, and a clip filmed while walking crosses several cells. From M6.6, the stated location is a set of the grid's finest cells (about 50 m across), which the uploader or code holder paints on the map, one tap per cell, as lawyers pick a case's area. The painted area is its own precision. Coordinates typed without JavaScript keep the three precisions, and stand for the finest cells of the hexagon they name: about the same area, since the small cells' outline is jagged. Existing clips are converted the same way by a migration, so each keeps about the same place. A stated location is at most 1 km across, measured as a case's area is, a ceiling fixed in the code with no way past it: a larger place would reach more cases, so more lawyers, and say more about the filmer's movements.

## Considered Options

- **One cell, with honest sizes**: the precisions labelled "about 50 m", "about 130 m" and "about 340 m", and the hexagon drawn on the map. No change to what is stored, but still one hexagon for a clip filmed along a street.
- **Cells snapped to the street tapped**, from the road shapes in the map's data: closer to "street", but the data has no outlines of neighbourhoods, and it adds a second way of choosing that is harder to get right. Left as an open question (SPEC §13).
- **Cells of any resolution in one set**: would keep the three precisions as cell sizes on the map, at the cost of matching and measuring sets of mixed sizes. The finest cells alone can draw any of them.
- **Dragging to paint on phones**: one finger would both move the map and paint, so a mode would be needed; a few taps cover a stretch of street.

## Consequences

- A painted path says more about the filmer than one place. It is their choice, the ceiling bounds it, and the map shows its size against the ceiling.
- Matching treats a clip's place as cases treat their areas: a clip matches when any of its cells overlaps the case's area or margin.
- The code holder edits the cells freely. The rule that a finer precision needs a new position (ADR 0004) is gone with the precisions: the edit page shows exactly the cells kept, not a centre that would invent precision.
- The precision of the metadata location, the GPS position kept from the file, is unchanged: one cell, which the code holder can make coarser, never finer.
- Without JavaScript, the edit page cannot show the cells as coordinates: typed coordinates replace them, and empty fields keep them.
- The map sends only cell ids, as the lawyers' map does: typed coordinates are now the only way a position reaches the server. This replaces what ADR 0006's addendum of 2026-10-31 says of the uploader's click, and, for the stated location, what ADR 0002's and ADR 0003's addenda of 2026-10-16 say of a cell's size following the precision; the metadata location still works that way.
