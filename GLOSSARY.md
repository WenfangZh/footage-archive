# Footage archive

An archive of phone videos of arrests and police operations at protests, made available to verified defence lawyers, one case at a time.

## Language

Each term has one fixed French term for the French interface, given under _French_. The _Avoid_ lists apply to the English terms; _Avoid in French_ lists French words that would be misread, mostly because they carry a legal meaning.

### People

**Filmer**:
The person who recorded a clip. Usually also the uploader and code holder, but the system never knows or assumes this.
_French_: personne qui a filmé
_Avoid_: Source, witness

**Uploader**:
The person uploading a clip, up to the moment the upload code is issued; from then on they are a code holder.
_French_: personne qui uploade
_Avoid_: Submitter, contributor
_Avoid in French_: déposant (a person giving testimony), mettre en ligne (means publishing)

**Code holder**:
Whoever presents the upload code for a clip. This may be several people, or someone other than the filmer, and the system cannot tell them apart.
_French_: personne qui détient le code
_Avoid_: Uploader (after upload), owner, user

**Applicant**:
A person invited to join as a lawyer who has not yet been admitted.
_French_: candidat·e
_Avoid_: Pending lawyer, candidate
_Avoid in French_: demandeur (the claimant in a lawsuit)

**Lawyer**:
An admitted defence lawyer whose access is not suspended. The only kind of person who can create cases or see clips.
_French_: avocat·e
_Avoid_: Member, user, avocat (in code and spec)

**Suspended lawyer**:
An admitted lawyer whose access the admin has paused. They see nothing until reinstated.
_French_: avocat·e à l'accès suspendu
_Avoid_: Inactive lawyer, disabled user
_Avoid in French_: avocat suspendu (a disciplinary suspension by the bar)

**Revoked lawyer**:
A former lawyer whose access the admin has ended for good. Their cases are closed, but nothing they did is erased.
_French_: avocat·e à l'accès révoqué
_Avoid_: Deleted lawyer, banned user
_Avoid in French_: radié (struck off the bar)

**Vouch**:
A trusted defence organisation's or an existing lawyer's statement that an applicant should be admitted; one of the conditions of admission.
_French_: parrainage
_Avoid_: Reference, sponsorship, endorsement
_Avoid in French_: caution (also bail), attestation (a witness statement in court)

**Admin**:
The person who manages lawyers' access (invitations, admissions, suspensions, revocations), approves oversized cases, deletes withdrawn clips and receives alerts. Not meant to see footage.
_French_: administrateur·rice
_Avoid_: Moderator, operator

### Upload codes

**Upload code**:
The secret given once to the uploader. It is the only way back to the clip, it covers exactly one clip, and it cannot be recovered if lost.
_French_: code d'upload
_Avoid_: Password, token, receipt, PIN

**Code card**:
The screen showing an upload code at the end of an upload, made to be screenshotted, with a card label.
_French_: fiche de code
_Avoid_: Receipt, confirmation page
_Avoid in French_: carte (also means map, and the upload form has one)

**Card label**:
Short text the uploader chooses to recognise a code card later. It exists only on the card, never on the server.
_French_: libellé de la fiche
_Avoid_: Label (alone), title, nickname

### Clips and their files

**Clip**:
One video in the archive, together with its times, locations, tags and hash record. The clip is the unit that case search returns.
_French_: clip
_Avoid_: Upload (as a noun), submission, video, footage (except for the material in general)

**Original**:
The clip's file exactly as received, before any processing. It is never served and never kept: it is deleted once the evidence copy is verified, when ingest finally fails, or when the uploader never confirms the form.
_French_: fichier d'origine
_Avoid_: Upload, quarantined upload, raw file

**Evidence copy**:
The clip's video and audio streams, unchanged, in a new container with no metadata of its own. It is never re-encoded, and it is what lawyers download. The clip's time, place and tags are held on the clip, not in this file.
_French_: copie de preuve
_Avoid_: Evidence file, stripped file, master

**Proxy**:
A low-resolution version of a clip for previewing. It is never evidence.
_French_: version de prévisualisation
_Avoid_: Preview file, thumbnail, transcode

**Hash record**:
The fingerprints of the original and of the evidence copy, with when each was taken. It shows the frames were not altered, and it outlives the clip's deletion.
_French_: empreintes numériques (hash)
_Avoid_: Checksum, signature
_Avoid in French_: relevé d'empreintes (fingerprinting a person in police custody)

**Ingest**:
The processing a clip goes through after upload, from checking the original to producing the evidence copy and proxy.
_French_: préparation (status: en préparation)
_Avoid_: Upload (as a noun), import, conversion
_Avoid in French_: traitement (data processing in the GDPR sense)

**Quarantine**:
Where an original waits, from the moment it is uploaded until ingest ends. It is never reachable from the web.
_French_: quarantaine
_Avoid_: Inbox, staging, temp

### What a clip says about itself

**Metadata time**:
The capture time found in the original's own metadata. Nobody can change it, and it is not trusted on its own: clocks can be wrong, apps overwrite it, and it is easy to forge.
_French_: heure des métadonnées
_Avoid_: Real time, true time, original time
_Avoid in French_: horodatage (kept for a future trusted timestamp)

**Stated time**:
The capture time given by the uploader, pre-filled with the metadata time, and as later edited by the code holder. Matching uses this time.
_French_: heure indiquée
_Avoid_: Corrected time, user time, manual time
_Avoid in French_: heure déclarée (suggests a formal declaration)

**Stated location**:
Where the clip was filmed, as given by the uploader or code holder, with how precise it is (exact spot, street or neighbourhood); it is never kept more precisely than that. Matching uses this location.
_French_: lieu indiqué
_Avoid_: Precise location, GPS, address

**Metadata location**:
The grid cell containing the GPS position found in the original, at a precision the uploader chooses, kept only to compare with the stated location.
_French_: lieu des métadonnées
_Avoid_: GPS, precise location

**Grid**:
The fixed worldwide grid of cells (about 50 m across by default) that every stored location is snapped to. No location is ever kept more precisely.
_French_: grille (a cell: maille)
_Avoid_: Tiles, zones
_Avoid in French_: case (for a cell; collides with the English Case)

**Tag**:
A short description of what can be seen in a clip, added by the code holder or by a lawyer. A **default tag** comes from the fixed list and is shown by its translated **tag name**; a **custom tag** is free text, stored as typed.
_French_: étiquette (étiquette prédéfinie, étiquette personnalisée, nom de l'étiquette)
_Avoid_: Category, label, keyword

**Tag source**:
Whether a tag was added by the code holder or by a lawyer. Lawyers see it; which lawyer is never shown to them.
_French_: provenance de l'étiquette
_Avoid_: Tag author, tag owner
_Avoid in French_: origine (already in fichier d'origine)

**Tag history**:
The admin's view of every tag added, proposed for removal and removed on one clip, with who and when.
_French_: historique des étiquettes
_Avoid_: Tag log, tag audit

**As uploaded**:
A clip's stated time, stated location and tags as they were at upload, kept unchanged so that later edits can be seen.
_French_: tel qu'uploadé
_Avoid_: Original values, first version

**Edit**:
A change the code holder makes to a clip after upload, recorded with which field changed and when.
_French_: modification
_Avoid_: Revision, update, correction

### Lawyer access

**Police report**:
The official record of an arrest or operation (procès-verbal) that a lawyer checks footage against, and from which a case's time window and area are taken.
_French_: procès-verbal
_Avoid_: PV, statement

**Case**:
A lawyer's search scope for one incident: a case label, a time window and an area taken from a police report. It belongs to exactly one lawyer and is not the legal proceeding itself; co-counsel on the same proceeding each have their own.
_French_: dossier de recherche
_Avoid_: Affaire, dossier, file, matter
_Avoid in French_: dossier or affaire alone (the legal proceeding), casier (criminal record), requête (an application to a court)

**Case label**:
Short text a lawyer chooses to recognise a case. Never a client's name.
_French_: libellé du dossier de recherche
_Avoid_: Label (alone), case name, reference

**Closed case**:
A case that can no longer be used to search, preview or download, closed by its lawyer, by revocation, or after a long period without use. It remains in the audit log.
_French_: dossier de recherche clos
_Avoid_: Archived case, deleted case
_Avoid in French_: classé (a case dropped by the prosecutor)

**Match**:
A clip matches a case when any part of the clip, by its stated time, falls inside the case's time window, and its stated location overlaps the case's area.
_French_: correspondance (a clip correspond à un dossier de recherche)
_Avoid_: Hit, result

**Message**:
A note between a lawyer and the code holder of a clip, carried by the archive. The code holder sees the sender as a verified defence lawyer, named only if the lawyer chooses.
_French_: message
_Avoid_: Contact request, chat

**Report**:
A lawyer's concern about a specific clip: wrong time or place, doubtful authenticity, or inappropriate content.
_French_: signalement de clip
_Avoid_: Flag, complaint
_Avoid in French_: signalement alone (also a suspect's description, or a report to the authorities)

**Doubt marker**:
A neutral notice shown to lawyers on a clip whose authenticity a lawyer has doubted, with no reason given and no verdict.
_French_: réserve sur l'authenticité
_Avoid_: Flag, fake warning

**Content warning**:
A notice shown to lawyers on a clip that one lawyer has reported as inappropriate. The clip stays visible.
_French_: avertissement de contenu
_Avoid_: Flag, strike

**Withdrawn**:
The state of a clip reported as inappropriate by two lawyers holding different cases: hidden from all search, pending the admin's decision to delete it.
_French_: retiré
_Avoid_: Hidden, removed, banned, suspended

**Alert**:
A notice to the admin of something needing attention: a lawyer's activity looks unusual (such as many downloads in a short period), or the ingest queue is backed up.
_French_: alerte
_Avoid_: Flag, warning

**Audit log**:
The permanent record of every action a lawyer takes in the archive. It can only be added to.
_French_: journal d'audit
_Avoid_: Access log, history

### Storage

**Index**:
The part of the archive the server can read and search, restricted to data that is harmless if exposed.
_French_: index
_Avoid_: Catalogue, metadata store

**Vault**:
The part of the archive holding everything sensitive, meant to become readable by lawyers only.
_French_: coffre
_Avoid_: Storage, private store
