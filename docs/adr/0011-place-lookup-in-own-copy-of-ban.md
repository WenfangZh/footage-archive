# Places are looked up in the archive's own copy of the Base Adresse Nationale

Few people know the coordinates of where they filmed, and police reports give addresses, not coordinates. From M9, the forms that take a place offer a **place lookup** by name. Uploaders and code holders look up **streets**; lawyers creating a case look up **addresses**, house numbers included. The data is the government's Base Adresse Nationale (BAN, Licence Ouverte 2.0) for the eight départements of Île-de-France, the area the map covers, downloaded by a management command and searched on the archive's own server with SQLite's full-text search. The lookup finds a place; it does not choose one. A street moves the map, where the uploader paints the cells as before (ADR 0009), or, without JavaScript, fills the typed coordinates, which the person can still change. An address moves a case's map and fills its typed place, whose radius the lawyer sets. Only what the forms already keep is kept: cells for a clip, cells for a case.

What is typed reaches the server in a request body, from the map on the same unlogged routes as the map data (ADR 0006), and is never stored or logged. For an uploader, that is no more than typed coordinates already send (ADR 0009, addendum of 2026-11-06), and a street says less than a house number, which is why uploaders get streets only. For a lawyer, a house number is finer than the grid, but no finer than the place and radius they already type from the police report (ADR 0006), and as transient.

## Considered Options

- **A geocoding service run by someone else** (the government's address API, Nominatim): no data to keep, typo tolerance, but every lookup would tell a third party, with the archive's or the person's IP address, a street where someone filmed or where a defence is looking. ADR 0006 keeps the map's requests on the archive for the same reason.
- **The street index in the browser**: nothing typed would leave the phone, but each uploader would download several megabytes on what may be a protest's crowded network, and house numbers (hundreds of megabytes) could not go that way at all.
- **The names in the map's own tiles**: no new data, but the tiles hold no index, split streets across tiles, and attach no street to house numbers.
- **A separate search service** (Photon, or Nominatim on our server): typo tolerance, at the cost of another service to run, for a data set SQLite's full-text search handles in milliseconds.
- **Typed coordinates only**: nothing new, but hardly anyone knows them.

## Consequences

- The data does not update itself: `fetch_address_data` downloads it again when run, out of git, into the archive's data folder, with the date it was published. A street built since then is missing until it runs again.
- The Licence Ouverte asks that the source and the date of the data be named: the licences page names both, the date read from the data downloaded.
- A street has no shape in the BAN: it stands for the address point nearest its centre, and the box around its addresses. Without JavaScript, the typed coordinates get that one point, with the precision "street", and the page says it is one point of the street.
- The lookup forgives accents, case and the usual abbreviations ("bd", "av."), and puts Paris first among places of the same name; it does not forgive typos.
- Without JavaScript, the forms gain a "Look up" button that sends the form back to its own page, with the matches listed; it never confirms an upload, saves an edit or creates a case. Error reports hide the query, as they hide the forms' other private fields.
- The lookup routes are rate-limited per connection and return at most ten matches: the data is public, but the lookup is not a bulk download.
- Typed coordinates stay, folded away, for browsers that cannot run the map and for anyone who has them.
