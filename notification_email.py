
#!/usr/bin/env python3
import hashlib
import html
import json
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

INVENTAIRE = Path("inventaire-gravel.json")
HISTORIQUE = Path("veille-notifications.json")
RESULTATS = Path("veille-resultats.json")
ULTRA = Path("ultra-veille.json")

DEPOT = os.environ.get(
    "GITHUB_REPOSITORY",
    "gabydelphine/Agenda-Gravel-autres-associations"
)


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", str(texte or ""))
    texte = "".join(
        caractere for caractere in texte
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"\s+", " ", texte).strip().casefold()


def identifiant(fiche):
    base = "|".join(
        normaliser(fiche.get(champ, ""))
        for champ in ("date", "title", "location")
    )
    return hashlib.sha256(
        base.encode("utf-8")
    ).hexdigest()[:16]


def charger(chemin, defaut):
    if not chemin.exists():
        return defaut
    return json.loads(chemin.read_text(encoding="utf-8"))


def est_a_examiner(fiche):
    statut = normaliser(fiche.get("statut", "a_verifier"))

    if statut not in ("a_verifier", "en_attente"):
        return False

    if not all(
        fiche.get(champ)
        for champ in ("date", "title", "location")
    ):
        return False

    try:
        date.fromisoformat(fiche["date"])
    except (ValueError, TypeError):
        return False

    titre = normaliser(fiche["title"])
    organisateur = normaliser(fiche.get("organizer", ""))

    if "occitanie 600" in titre or "ogravel" in organisateur:
        return False

    controle_zone = normaliser(fiche.get("controle_zone", ""))

    if controle_zone in (
        "hors_zone", "hors zone", "exclu", "hors rayon"
    ):
        return False

    distance = fiche.get("distance_toulouse_km")
    if distance is not None:
        try:
            if float(distance) > 200:
                return False
        except (TypeError, ValueError):
            return False

    return True


def lien_decision(fiche, decision):
    titre = "GRAVEL {} {}".format(
        decision.upper(),
        identifiant(fiche)
    )

    corps = (
        "Manifestation : {title}\n"
        "Date : {date}\n"
        "Lieu : {location}\n"
        "Source : {source}\n\n"
        "Décision demandée : {decision}\n\n"
        "Créer cette issue pour confirmer la décision."
    ).format(
        title=fiche.get("title", ""),
        date=fiche.get("date", ""),
        location=fiche.get("location", ""),
        source=fiche.get("source", ""),
        decision=decision.upper()
    )

    parametres = urlencode({
        "title": titre,
        "body": corps
    })

    return (
        "https://github.com/"
        + DEPOT
        + "/issues/new?"
        + parametres
    )


def lien_ultra(fiche, decision, texte):
    """Lien GitHub qui crée la demande « ULTRA AJOUTER|REFUSER|DATE <id> »."""
    corps = (
        f"Calendrier Ultra gravel : {fiche.get('n', '')}\n"
        f"Date : {fiche.get('d', '')}\n"
        f"Source : {fiche.get('s', '')}\n\n"
        f"Décision demandée : {texte}\n\n"
        "Créer cette issue pour confirmer la décision."
    )
    return ("https://github.com/" + DEPOT + "/issues/new?"
            + urlencode({"title": f"ULTRA {decision} {fiche['id']}", "body": corps}))


def bouton(lien, texte, couleur):
    return ('<a href="{}" style="display:inline-block;padding:10px 16px;background:{};'
            'color:white;text-decoration:none;border-radius:5px;margin-right:6px;">{}</a>'
            ).format(echapper(lien), couleur, texte)


def echapper(valeur):
    return html.escape(str(valeur or ""), quote=True)


def main():
    if not INVENTAIRE.exists():
        print("Inventaire absent : aucun e-mail envoyé.")
        raise SystemExit(1)

    inventaire = charger(INVENTAIRE, {"events": []})
    fiches = inventaire.get("events", [])

    if not isinstance(fiches, list):
        raise SystemExit("Format d'inventaire incorrect.")

    historique = charger(HISTORIQUE, {"envoyes": []})
    deja_envoyes = set(historique.get("envoyes", []))

    nouveautes = []
    ids_vus = set()

    for fiche in fiches:
        if not isinstance(fiche, dict):
            continue

        if not est_a_examiner(fiche):
            continue

        numero = identifiant(fiche)
        cle_historique = "gravel:" + numero

        if numero in ids_vus:
            continue

        ids_vus.add(numero)

        if cle_historique in deja_envoyes:
            continue

        nouveautes.append(fiche)

    # Annonces gravel datées dont la commune n'a pas été reconnue :
    # elles sont signalées pour un coup d'œil, sans bouton de décision.
    pistes = []
    for piste in charger(RESULTATS, {}).get("a_examiner", []):
        cle_piste = "piste:" + str(piste.get("date")) + "|" + str(piste.get("source"))
        if cle_piste not in deja_envoyes:
            pistes.append((cle_piste, piste))
    pistes = pistes[:40]

    # Calendrier Ultra gravel : nouvelles épreuves et dates précisées (veille_ultra.py).
    ultra = charger(ULTRA, {})
    ultra_nouv = [f for f in ultra.get("nouveautes", []) if "ultra:" + f["id"] not in deja_envoyes]
    ultra_dates = [f for f in ultra.get("mises_a_jour", []) if "ultra:" + f["id"] not in deja_envoyes]

    if not nouveautes and not pistes and not ultra_nouv and not ultra_dates:
        print("Aucune nouvelle manifestation à notifier.")
        return

    morceaux_html = [
        "<html><body>",
        '<div style="font-family:Arial,sans-serif;'
        'max-width:650px;margin:auto;">',
        "<h2>Veille Gravel Toulouse</h2>",
        "<p>Voici les nouvelles manifestations à examiner.</p>"
        if nouveautes else "<p>Aucune nouvelle fiche complète cette semaine.</p>",
        "<p><strong>Aucune publication automatique.</strong> "
        "Chaque bouton ouvre GitHub : il faudra "
        "confirmer la création de la demande.</p>"
    ]

    lignes_texte = [
        "Bonjour,",
        "",
        "Voici les nouvelles manifestations à examiner.",
        "Aucune publication automatique.",
        "Les liens ouvrent GitHub pour confirmer la décision.",
        ""
    ]

    for numero, fiche in enumerate(nouveautes, 1):
        titre = fiche.get("title", "")
        jour = fiche.get("date", "")
        lieu = fiche.get("location", "")
        details = fiche.get("details", "")
        source = fiche.get("source", "")

        valider = lien_decision(fiche, "valider")
        refuser = lien_decision(fiche, "refuser")

        morceaux_html.extend([
            '<div style="border-top:1px solid #ddd;'
            'padding:18px 0;">',
            f"<h3>{numero}. {echapper(titre)}</h3>",
            f"<p><strong>Date :</strong> {echapper(jour)}<br>",
            f"<strong>Lieu :</strong> {echapper(lieu)}</p>",
            f"<p>{echapper(details)}</p>"
        ])

        if source:
            morceaux_html.append(
                '<p><a href="{}">Voir la source</a></p>'.format(
                    echapper(source)
                )
            )

        morceaux_html.extend([
            '<p><a href="{}" style="display:inline-block;'
            'padding:12px 18px;background:#207245;'
            'color:white;text-decoration:none;'
            'border-radius:5px;">Valider</a> '
            '<a href="{}" style="display:inline-block;'
            'padding:12px 18px;background:#a83232;'
            'color:white;text-decoration:none;'
            'border-radius:5px;">Refuser</a></p>'.format(
                echapper(valider),
                echapper(refuser)
            ),
            "</div>"
        ])

        lignes_texte.extend([
            f"{numero}. {titre}",
            f"Date : {jour}",
            f"Lieu : {lieu}",
            f"Détails : {details}",
            f"Source : {source}",
            f"Valider : {valider}",
            f"Refuser : {refuser}",
            ""
        ])

    if pistes:
        morceaux_html.append(
            '<h2 style="margin-top:30px;">Pistes à regarder (lieu non reconnu)</h2>'
            "<p>Annonces gravel datées trouvées par la veille, mais dont la commune "
            "n'a pas pu être identifiée. Si l'une d'elles vous intéresse, ajoutez-la "
            "à la main dans events.json.</p><ul>"
        )
        lignes_texte.extend(["", "PISTES À REGARDER (lieu non reconnu)", ""])
        for _, piste in pistes:
            morceaux_html.append(
                '<li style="margin-bottom:8px;">{} — <a href="{}">{}</a>{}</li>'.format(
                    echapper(piste.get("date")), echapper(piste.get("source")),
                    echapper(piste.get("title")),
                    (" (" + echapper(piste.get("raison")) + ")") if piste.get("raison") else ""
                )
            )
            lignes_texte.append(f"{piste.get('date')} — {piste.get('title')} — {piste.get('source')}")
        morceaux_html.append("</ul>")

    if ultra_nouv or ultra_dates:
        morceaux_html.append(
            '<h2 style="margin-top:30px;color:#2f6f73;">Calendrier Ultra gravel</h2>'
            "<p>Épreuves gravel ou bikepacking de 150 km et plus, départ à moins de 350 km de Toulouse.</p>"
        )
        lignes_texte.extend(["", "CALENDRIER ULTRA GRAVEL", ""])
        for f in ultra_nouv:
            km = ", ".join(f"{k} km" for k in f.get("k", []))
            morceaux_html.extend([
                '<div style="border-top:1px solid #ddd;padding:14px 0;">',
                f"<h3>{echapper(f.get('n'))}</h3>",
                f"<p><strong>Date :</strong> {echapper(f.get('d'))}"
                + (f" au {echapper(f.get('f'))}" if f.get("f") else "") + "<br>",
                f"<strong>Lieu :</strong> {echapper(f.get('l'))} "
                f"({echapper(f.get('distance_toulouse_km'))} km de Toulouse)<br>",
                f"<strong>Distances :</strong> {echapper(km)} · {echapper(f.get('t'))}</p>",
                f'<p><a href="{echapper(f.get("s"))}">Voir la source</a></p>',
                "<p>" + bouton(lien_ultra(f, "AJOUTER-ASSO", "ajouter, organisée par une association"),
                               "Ajouter (association)", "#2f6f73")
                + bouton(lien_ultra(f, "AJOUTER-PRIVE", "ajouter, organisateur privé"), "Ajouter (privé)", "#647169")
                + bouton(lien_ultra(f, "REFUSER", "ne plus proposer"), "Ignorer", "#a83232") + "</p>",
                "</div>",
            ])
            lignes_texte.extend([f"{f.get('n')} — {f.get('d')} — {f.get('l')} — {km}",
                                 f"Source : {f.get('s')}",
                                 f"Ajouter (association) : {lien_ultra(f, 'AJOUTER-ASSO', 'ajouter, organisée par une association')}",
                                 f"Ajouter (privé) : {lien_ultra(f, 'AJOUTER-PRIVE', 'ajouter, organisateur privé')}",
                                 f"Ignorer : {lien_ultra(f, 'REFUSER', 'ne plus proposer')}", ""])
        for f in ultra_dates:
            morceaux_html.extend([
                '<div style="border-top:1px solid #ddd;padding:14px 0;">',
                f"<h3>Date précisée : {echapper(f.get('n'))}</h3>",
                f"<p>Affiché aujourd'hui : {echapper(f.get('d_actuelle'))}<br>"
                f"Trouvé sur la source : <strong>{echapper(f.get('d'))}</strong>"
                + (f" au {echapper(f.get('f'))}" if f.get("f") else "") + "</p>",
                f'<p><a href="{echapper(f.get("s"))}">Voir la source</a></p>',
                "<p>" + bouton(lien_ultra(f, "DATE", "mettre la date à jour"), "Mettre à jour", "#2f6f73")
                + bouton(lien_ultra(f, "REFUSER", "garder la date actuelle"), "Ignorer", "#a83232") + "</p>",
                "</div>",
            ])
            lignes_texte.extend([f"Date précisée : {f.get('n')} → {f.get('d')}",
                                 f"Mettre à jour : {lien_ultra(f, 'DATE', 'mettre la date à jour')}", ""])

    morceaux_html.extend([
        "<p>Veille Gravel Toulouse</p>",
        "</div></body></html>"
    ])

    message = {
        "sender": {
            "name": "Veille Gravel Toulouse",
            "email": os.environ["BREVO_SENDER_EMAIL"]
        },
        "to": [{
            "email": os.environ["NOTIFICATION_EMAIL"]
        }],
        "subject": (
            f"Veille Gravel - "
            f"{len(nouveautes)} manifestation(s) à valider"
            + (f", {len(pistes)} piste(s)" if pistes else "")
            + (f", {len(ultra_nouv) + len(ultra_dates)} pour le calendrier Ultra"
               if ultra_nouv or ultra_dates else "")
        ),
        "htmlContent": "\n".join(morceaux_html),
        "textContent": "\n".join(lignes_texte)
    }

    requete = Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(message).encode("utf-8"),
        headers={
            "api-key": os.environ["BREVO_API_KEY"],
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        method="POST"
    )

    try:
        with urlopen(requete, timeout=30) as reponse:
            print(
                f"Envoi accepté par Brevo : HTTP {reponse.status}"
            )
    except HTTPError as erreur:
        print(f"Erreur Brevo : HTTP {erreur.code}")
        raise SystemExit(1)
    except URLError as erreur:
        print(f"Connexion impossible : {erreur.reason}")
        raise SystemExit(1)

    deja_envoyes.update(
        "gravel:" + identifiant(fiche)
        for fiche in nouveautes
    )
    deja_envoyes.update(cle for cle, _ in pistes)
    deja_envoyes.update("ultra:" + f["id"] for f in ultra_nouv + ultra_dates)

    HISTORIQUE.write_text(
        json.dumps(
            {"envoyes": sorted(deja_envoyes)},
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print(
        f"{len(nouveautes)} manifestation(s) "
        "marquée(s) comme notifiée(s)."
    )


if __name__ == "__main__":
    main()
