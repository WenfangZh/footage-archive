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

**Invitation**:
The admin's offer to a person to join as a lawyer, sent by email. It makes them an applicant; only admission makes them a lawyer.
_French_: invitation
_Avoid_: Sign-up, registration, invite (as a noun)
_Avoid in French_: convocation (a summons)

**Applicant**:
A person invited to join as a lawyer who has not yet been admitted.
_French_: candidat·e
_Avoid_: Pending lawyer, candidate
_Avoid in French_: demandeur (the claimant in a lawsuit)

**Declined applicant**:
An applicant the admin has decided not to admit. Their name, bar and email address are erased.
_French_: candidat·e non retenu·e (the act: candidature non retenue)
_Avoid_: Rejected applicant, refused applicant
_Avoid in French_: rejeté (the dismissal of a claim or an appeal)

**Lawyer**:
An admitted defence lawyer whose access is not suspended. The only kind of person who can create cases or see clips.
_French_: avocat·e
_Avoid_: Member, user, avocat (in code and spec)

**Suspended lawyer**:
An admitted lawyer whose access the admin has paused. They see nothing until reinstated.
_French_: avocat·e à l'accès suspendu (the act: suspension de l'accès)
_Avoid_: Inactive lawyer, disabled user
_Avoid in French_: avocat suspendu (a disciplinary suspension by the bar), suspension alone (also an officer's suspension)

**Reinstatement**:
The admin's ending of a suspension: the lawyer regains the access they had.
_French_: rétablissement de l'accès
_Avoid_: Reactivation, unsuspension
_Avoid in French_: réintégration (a return to the bar, or to a job)

**Revoked lawyer**:
A former lawyer whose access the admin has ended for good. Their cases are closed, but nothing they did is erased.
_French_: avocat·e à l'accès révoqué (the act: révocation de l'accès)
_Avoid_: Deleted lawyer, banned user
_Avoid in French_: radié (struck off the bar), révocation alone (a civil servant's dismissal)

**Vouch**:
A trusted defence organisation's or an existing lawyer's statement that an applicant should be admitted; one of the conditions of admission.
_French_: parrainage
_Avoid_: Reference, sponsorship, endorsement
_Avoid in French_: caution (also bail), attestation (a witness statement in court)

**Admission**:
The admin's decision that makes an applicant a lawyer, once their registration with a bar is verified and they have a vouch.
_French_: admission
_Avoid_: Approval (alone), activation, onboarding
_Avoid in French_: inscription (a lawyer's registration with the bar itself)

**Admin**:
The person who manages lawyers' access (invitations, admissions, declining applicants, suspensions, reinstatements, revocations), approves oversized cases, deletes withdrawn clips and receives alerts. Not meant to see footage.
_French_: administrateur·rice
_Avoid_: Moderator, operator

**Two-factor authentication**:
The code an applicant, a lawyer or the admin gives after their password, from an authenticator app on their phone (a six-digit code), or a recovery code.
_French_: double authentification (its codes: code à six chiffres, code de secours)
_Avoid_: 2FA in the interface, MFA, OTP
_Avoid in French_: code alone (the upload code, for code holders)

**Recovery code**:
A single-use code an applicant, a lawyer or the admin keeps, to log in once if they lose the phone that gives their second factor.
_French_: code de secours
_Avoid_: Backup code, upload code
_Avoid in French_: code alone (the upload code, for code holders)

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
_French_: préparation (statuses: en préparation, prêt, échec de la préparation)
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
_French_: lieu indiqué (its precision: endroit exact, rue or quartier)
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

**Context**:
Free text about a clip from the uploader or code holder: what happened, and anything a lawyer should know.
_French_: contexte
_Avoid_: Description, comment, notes

**Contact details**:
A way to reach the code holder, such as a phone number or a social media handle, that they chose to leave despite the risk.
_French_: moyen de contact
_Avoid_: Contact info, identity
_Avoid in French_: coordonnées (also the coordinates of a position, which the same forms ask for)

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

**Time window**:
The stretch of time a case covers, from its start to its end, taken from the police report. Fixed when the case is created.
_French_: plage horaire
_Avoid_: Period, time range, timeframe

**Area**:
The place a case covers: a set of the grid's finest cells, made from the place in the police report and a radius, or picked on a map. Fixed when the case is created; the coordinates typed to make it are not kept. Its size is how far across it is: between its two farthest points, edge to edge.
_French_: périmètre
_Avoid_: Zone, perimeter, region, radius (the radius is only one way to make the area)
_Avoid in French_: zone (the interface already uses it for a grid cell)

**Radius**:
How far around the police report's place a lawyer wants the area to reach, typed when creating a case. The area is made to contain that whole circle; neither the place nor the radius is kept.
_French_: rayon

**Ring**:
One layer of the grid's cells around a cell or a set of cells. A case's margin around its area is counted in rings.
_French_: anneau (de mailles)
_Avoid_: Band, buffer

**Map**:
Where a lawyer can pick a case's area cell by cell, instead of typing a place and a radius. It shows streets and place names from the archive's own map data, never clips.
_French_: carte

**Map data**:
What the map is drawn from: its tiles, style and fonts, all served by the archive itself and only to lawyers.
_French_: données cartographiques
_Avoid_: Tiles alone (could be taken for the grid's cells), basemap

**Closed case**:
A case that can no longer be used to search, preview or download, closed by its lawyer, by revocation, or after a long period without use. It remains in the audit log.
_French_: dossier de recherche clos
_Avoid_: Archived case, deleted case
_Avoid in French_: classé (a case dropped by the prosecutor)

**Match**:
A clip matches a case when any part of the clip, by its stated time, falls inside the case's time window, and its stated location overlaps the case's area, each widened by the case's margin. A match is either a core match or a margin match.
_French_: correspondance (a clip correspond à un dossier de recherche)
_Avoid_: Hit, result

**Core match**:
A match inside the case's time window and area themselves, without the margin.
_French_: correspondance centrale
_Avoid_: Priority 1 result, primary match

**Margin match**:
A match found only in the case's margin: just outside its time window or its area.
_French_: correspondance de marge
_Avoid_: Priority 2 result, secondary match

**Size caps**:
The largest time window and area a case may have without the admin's approval (by default 2 hours and 2 km across, margin included).
_French_: plafonds
_Avoid_: Limits (alone), quota

**Case approval**:
The admin's decision on a case over the size caps: approved, the case can be used; refused, it is closed. Until then the case waits and cannot be used. A case within the size caps needs none.
_French_: validation du dossier de recherche
_Avoid_: Authorisation, permission, sign-off

**Lawyer's reason**:
What a lawyer writes to explain why a case must be over the size caps. The admin reads it to decide; it is deleted once they have, or once the case is closed. Never a client's name.
_French_: justification de la demande
_Avoid_: Context (a clip's own), justification (alone)

**Ceiling**:
The size no case may exceed, even with the admin's approval: 24 hours and 10 km across, margin included. It is fixed in the code, so raising it takes a public change; the size caps can be set no higher than it. Not to be confused with the size caps (plafonds), which approval can exceed.
_French_: limite absolue
_Avoid_: Maximum, hard cap

**Margin**:
The band a case adds around its time window and its area, fixed when the case is created (by default 10 minutes, and one ring of the grid's finest cells); each lawyer sets their own default. The time window and area, margin included, must fit the size caps.
_French_: marge
_Avoid_: Padding, buffer, tolerance

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
A notice to the admin of something needing attention: a lawyer's activity looks unusual (such as many downloads in a short period), logins for one account keep failing, or the ingest queue is backed up.
_French_: alerte
_Avoid_: Flag, warning

**Alert limits**:
The numbers past which an alert is raised, such as how many failed logins for one account, how many clips waiting for ingest, or how many downloads or new cases by one lawyer in a short time. Set by the server's configuration, and a change is recorded in the audit log when the archive starts; the admin can raise one lawyer's limit for a few days, which is logged too.
_French_: seuils d'alerte
_Avoid_: Thresholds (alone), triggers
_Avoid in French_: seuil alone (also the gap between the metadata time and stated time that is highlighted)

**Audit log**:
The permanent record of every action a lawyer takes in the archive, of the admin's actions on applicants' and lawyers' accounts, decisions on larger cases and raises of a lawyer's alert limit, and of changes to the size caps and alert limits. It can only be added to.
_French_: journal d'audit
_Avoid_: Access log, history

### Storage

**Index**:
The part of the archive the server can read and search, restricted to data that is harmless if exposed.
_French_: index
_Avoid_: Catalogue, metadata store

**Vault**:
The part of the archive holding everything sensitive, encrypted so that only lawyers' keys open it (and a code holder's own texts, their upload code).
_French_: coffre
_Avoid_: Storage, private store

### Keys

**Personal key**:
A lawyer's own key, made in their browser and never readable by the server. It opens their key grant, and the case labels sealed to it.
_French_: clé personnelle
_Avoid_: Account key, private key (in the interface)

**Key passphrase**:
The secret a lawyer types in their browser to unlock their personal key, generated by the browser when the key is made (never chosen). It is not their login password, and no one can recover it for them.
_French_: phrase secrète
_Avoid_: Password, PIN
_Avoid in French_: mot de passe (the login password)

**Recovery key**:
A long secret generated and shown once when a lawyer makes their personal key, kept on paper, that unlocks the personal key if the key passphrase is forgotten.
_French_: clé de récupération
_Avoid_: Recovery code (a single-use code for logging in), backup key
_Avoid in French_: clé de secours, too close to code de secours

**Key reset**:
Retiring a lawyer's personal key when they have lost both its key passphrase and its recovery key, started from their own account. They then set up a new key, which the admin confirms and a lawyer grants the group key to, as for a newcomer; what was sealed only to the old key, such as case labels, is lost.
_French_: réinitialisation de la clé
_Avoid_: Recovery (which keeps the key), rotation (the group key's)
_Avoid in French_: renouvellement (the group key's rotation)

**Safety number**:
A short series of digits computed from a personal key, which two people compare by phone or in person to check that the key the archive shows is the one the lawyer made.
_French_: numéro de sécurité
_Avoid_: Fingerprint (the hash record's fingerprints), key ID
_Avoid in French_: empreinte (the hash record)

**Group key**:
The key all lawyers share, which opens every clip's evidence copy, proxy, context and contact details. Ingest encrypts to it without being able to decrypt.
_French_: clé commune
_Avoid_: Master key, archive key, team key

**Key grant**:
A copy of the group key sealed to one lawyer's personal key. A newcomer's is made in another lawyer's browser after both the admin and that lawyer have compared the newcomer's safety number with them; the first lawyer grants the first group key to themselves, and a rotation makes new grants for every lawyer who held one.
_French_: remise de la clé commune
_Avoid_: Invitation (to the archive), share, access (alone)

**Rotation**:
Replacing the group key, after a revocation or a suspected exposure of a lawyer's key, and resealing every data key to the new one, done in the rotator's browser; the old key goes once two checkers have each passed a check of the new one. A rotation may take several **attempts** (_French_: tentatives), each with a rotator and checkers drawn afresh.
_French_: renouvellement de la clé commune
_Avoid_: Re-encryption (the files themselves are not re-encrypted), reset

**Rotator**:
The lawyer whose browser carries out a rotation, drawn at random among lawyers who recently unlocked their key.
_French_: personne qui renouvelle la clé commune
_Avoid_: Admin (who never rotates), rekeyer

**Checker**:
A lawyer drawn at random, never the rotator, whose browser checks a rotation's work without them doing anything. Two must pass before the old group key goes; a third, the confirmer, is drawn when one reports a failure.
_French_: personne qui vérifie le renouvellement
_Avoid_: Reviewer, verifier, approver
_Avoid in French_: contrôleur (a legal office), vérificateur

**Confirmer**:
A further lawyer drawn at random, outside the rotator and both checkers, to recheck what one checker reported as failed. If they disagree, the attempt is given up and a new one drawn.
_French_: personne qui revérifie le renouvellement
_Avoid_: Arbiter, judge, referee
_Avoid in French_: confirmer (the admin confirms a lawyer's key)

**Data key**:
The random key that encrypts one item in the vault (a file, a text or a case label). It is never stored as it is, only sealed to the group key, or to a lawyer's personal key or the code holder key.
_French_: clé de l'élément
_Avoid_: File key, content key

**Code holder key**:
The key derived from a clip's upload code, which opens the context and contact details addressed to the code holder. The server stores only its public half.
_French_: clé du code
_Avoid_: Uploader key
