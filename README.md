# Agenda Gravel — 200 km autour de Toulouse

Agenda des manifestations gravel organisées par d'autres associations, dans un rayon de 200 km (à vol d'oiseau) autour de Toulouse. La page publique lit `events.json`.

## Comment fonctionne la veille

Chaque jour (GitHub Actions, `veille_gravel.py`) :

1. **Sites surveillés** : la liste est dans `sources.json`. Pour en ajouter un, copier une ligne de `sites` et changer le nom et l'adresse — aucune programmation nécessaire.
2. **Recherche web** : des requêtes Brave Search (« rando gravel Gers 2027 », « gravel site:helloasso.com »…) découvrent les pages que personne n'a signalées. Nécessite le secret `BRAVE_API_KEY` ; sans lui, cette étape est simplement sautée.
3. **Lecture** : données structurées des pages (schema.org, iCal) en priorité, sinon lecture du texte, des calendriers et des PDF ligne par ligne. La veille respecte les fichiers robots.txt des sites.
4. **Lieu** : la commune est reconnue grâce à la base officielle des communes (geo.api.gouv.fr), conservée dans `cache/communes-zone.json` et actualisée tous les 4 mois.
5. **Tri** : les nouveautés entrent dans `inventaire-gravel.json` avec le statut `a_verifier`. Les annonces gravel datées dont la commune n'est pas reconnue ne sont pas jetées : elles restent dans `veille-resultats.json` (rubrique `a_examiner`).

Le dimanche, un e-mail récapitule les fiches à valider (boutons Valider / Refuser) et les pistes sans lieu reconnu. **Rien n'est publié sans votre validation.** Une fiche refusée n'est jamais reproposée.

## Ajouter un événement à la main

Modifier `events.json` sur GitHub, puis **Commit changes**. Champs : `date` (AAAA-MM-JJ), `end_date` (facultatif), `title`, `location`, `details`, `source`.

Les informations peuvent évoluer : vérifier les liens sources avant de se déplacer.
