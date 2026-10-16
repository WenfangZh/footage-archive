# No location stored more precisely than the grid

The archive never stores a location more precise than its grid (50 m by default): the GPS position in a file is snapped to a grid cell during ingest and the precise value is discarded, and the location used for matching is the stated location given by the uploader or code holder, with a precision (exact spot, street or neighbourhood). Precise GPS shows where the filmer stood, which for a clip filmed from a window is their home, and the most at-risk people here are filmers and minors. Lawyers lose the ability to pinpoint a filmer's position, but the footage itself shows what happened, which is what they need. Once discarded at ingest the precise position can never be recovered, so reversing this would only help future clips.

## Considered Options

- **Precise GPS in the vault** (encrypted to lawyers from M5): rejected because a breach, seizure or compelled disclosure before or despite encryption would expose filmers' positions.
- **Snapping the stated location to the grid**: rejected because a stated place like "Bastille" is vaguer than a grid cell, and snapping would invent precision; a stated precision is kept instead.

## Addendum (2026-10-16)

The grid is no longer a 50 m square grid: locations are snapped to H3 cells at resolution 11, about 50 m across (44–50 m over metropolitan France), and the resolution can be made coarser but never finer. ADR 0003 gives the reasons.

## Addendum (2026-10-16): cell size follows the precision

The stated location is now snapped to the grid, but to a cell no finer than its stated precision, so this is not the snapping rejected above; the uploader chooses the cell size of the metadata location. ADR 0004 gives the reasons.
