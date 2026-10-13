
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse
from html import unescape
from io import BytesIO

from pypdf import PdfReader

SOURCES = {
    "FSGT 31": "https://cyclismefsgt31.fr/",
    "FFVelo 31": "https://haute-garonne.ffvelo.fr/",
    "Cyclotourisme 31": "https://www.cyclotourisme-31.com/",
    "FFVelo 82": "https://cyclotourisme82-ffct.fr/",
    "Velo Occitanie": "https://www.velo-occitanie.fr/",
    "Saubens Cyclo": "https://saubenscyclo31.fr/",
}

PDF_CONNUS = {
    "Circuit Gravel Saubens 2026":
        "https://www.cyclotourisme-31.com/"
        "uploaded/Circuits_InterClubs/2026/"
        "saubensgravel2026.pdf",
}

FICHIER = Path("veille-resultats.json")

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11,
    "decembre": 12,
}

def telecharger(url):
    requete = Request(
        url, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urlopen(requete, timeout=25) as reponse:
        return reponse.read(10_000_001)

def nettoyer_html(contenu):
    html = contenu.decode("utf-8", errors="replace")
    html = re.sub(
        r"(?is)<(script|style|nav|footer)\b[^>]*>.*?</\1>",
        " ", html
    )
    html = re.sub(r"(?i)</(?:div|article|li|p|h[1-6])>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    return unescape(html)

def lire_pdf(contenu):
    lecteur = PdfReader(BytesIO(contenu))
    return "\n".join(
        page.extract_text() or ""
        for page in lecteur.pages[:15]
    )

def extraire_liens_pdf(contenu, base):
    html = contenu.decode("utf-8", errors="replace")
    liens = re.findall(
        r"""href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']""",
        html, re.IGNORECASE
    )
    return {
        urljoin(base, unescape(lien))
        for lien in liens
    }

def trouver_date(texte):
    texte = texte.lower()
    texte = (
        texte.replace("é", "e")
        .replace("è", "e")
        .replace("û", "u")
        .replace("ô", "o")
    )

    motif = (
        r"\b(\d{1,2})\s+("
        + "|".join(MOIS)
        + r")\s+(20\d{2})\b"
    )

    match = re.search(motif, texte)
    if match:
        jour, mois, annee = match.groups()
        try:
            return datetime(
                int(annee), MOIS[mois], int(jour)
            ).date().isoformat()
        except ValueError:
            pass

    return None

def enregistrer(nom, url, extrait, observations, connus):
    extrait = re.sub(r"\s+", " ", extrait).strip()

    if not re.search(r"\bgravel\b", extrait, re.I):
        return

    if len(extrait) < 30:
        return

    identifiant = url + "|" + extrait.casefold()

    if identifiant in connus:
        return

    observations.append({
        "source": nom,
        "url": url,
        "extrait": extrait[:500],
        "date_evenement": trouver_date(extrait),
        "date_detection": datetime.now(
            timezone.utc
        ).date().isoformat(),
        "statut": "a_verifier"
    })
    connus.add(identifiant)

if FICHIER.exists():
    donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
else:
    donnees = {"observations": []}

observations = donnees.get("observations", [])
connus = {
    item.get("url", "") + "|" +
    item.get("extrait", "").casefold()
    for item in observations
}

pdf_a_consulter = dict(PDF_CONNUS)

for nom, url in SOURCES.items():
    try:
        contenu = telecharger(url)
        texte = nettoyer_html(contenu)
  
        if nom == "Velo Occitanie":
            # On découpe les annonces à partir de leur date.
            # Le nom précède la date et les parcours la suivent.
            texte_normalise = re.sub(r"\s+", " ", texte)

            motif_date = (
                r"\b(?:Lun|Mar|Mer|Jeu|Ven|Sam|Dim)\.?\s+"
                r"\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2}\b"
            )

            dates = list(
                re.finditer(motif_date, texte_normalise, re.I)
            )

            for i, match in enumerate(dates):
                fin = (
                    dates[i + 1].start()
                    if i + 1 < len(dates)
                    else len(texte_normalise)
                )

                bloc = texte_normalise[match.end():fin]

                # "Détails" marque la fin de l'annonce.
                bloc = re.split(
                    r"\bDétails\b", bloc, maxsplit=1,
                    flags=re.IGNORECASE
                )[0]

                if not re.search(r"\bGravel\b", bloc, re.I):
                    continue

                # Le titre est situé avant la date.
                avant_date = texte_normalise[
                    max(0, match.start() - 120):match.start()
                ]

                titre = avant_date.split("Détails")[-1].strip()

                if not titre or len(titre) > 100:
                    continue

                date_evenement = trouver_date(match.group())

                if not date_evenement:
                    continue

                extrait = (
                    f"Détails {titre} {match.group()} {bloc}"
                )

                enregistrer(
                    nom,
                    url,
                    extrait[:500],
                    observations,
                    connus
                )

                print(
                    f"Gravel detecte : {titre} "
                    f"({date_evenement})"
                )

        for lien in extraire_liens_pdf(contenu, url):
            if urlparse(lien).netloc == urlparse(url).netloc:
                pdf_a_consulter[lien] = lien

        print(f"Site consulte : {nom}")

    except Exception as erreur:
        print(f"Erreur site {nom} : {erreur}")

for nom, url in pdf_a_consulter.items():
    try:
        contenu = telecharger(url)

        if len(contenu) > 10_000_000:
            print(f"PDF trop volumineux : {url}")
            continue

        texte = lire_pdf(contenu)
        lignes = [
            re.sub(r"\s+", " ", ligne).strip()
            for ligne in texte.splitlines()
        ]

        for i, ligne in enumerate(lignes):
            if re.search(r"\bgravel\b", ligne, re.I):
                extrait = " ".join(
                    lignes[max(0, i - 3):i + 4]
                )
                enregistrer(
                    nom, url, extrait,
                    observations, connus
                )

        print(f"PDF consulte : {url}")

    except Exception as erreur:
        print(f"Erreur PDF {url} : {erreur}")

FICHIER.write_text(
    json.dumps(
        {"observations": observations},
        ensure_ascii=False,
        indent=2
    ) + "\n",
    encoding="utf-8"
)

print(f"Total observations : {len(observations)}")

