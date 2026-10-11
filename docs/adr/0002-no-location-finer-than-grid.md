# No location stored more precisely than the grid

The archive never stores a location more precise than its grid (50 m by default): the GPS position in a file is snapped to a grid cell during ingest and the precise value is discarded, and the location used for matching is the stated location given by the uploader or code holder, with a precision (exact spot, street or neighbourhood). Precise GPS shows where the filmer stood, which for a clip filmed from a window is their home, and the most at-risk people here are filmers and minors. Lawyers lose the ability to pinpoint a filmer's position, but the footage itself shows what happened, which is what they need. Once discarded at ingest the precise position can never be recovered, so reversing this would only help future clips.

## Considered Options

- **Precise GPS in the vault** (encrypted to lawyers from M5): rejected because a breach, seizure or compelled disclosure before or despite encryption would expose filmers' positions.
- **Snapping the stated location to the grid**: rejected because a stated place like "Bastille" is vaguer than a grid cell, and snapping would invent precision; a stated precision is kept instead.
