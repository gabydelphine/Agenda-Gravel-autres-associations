
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
        "https://www.cyclotourisme-31.com/uploaded/Circuits_InterClubs/2026/saubensgravel2026.pdf",
}

MOTS_CLES = (
    "gravel", "grvl", "graviers",
    "ronde saubenoise", "la saubenoise",
    "la plaisancoise", "raid du lys",
    "la mailhocoise",
)

FICHIER = Path("veille-resultats.json")


def telecharger(url):
    requete = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )
    with urlopen(requete, timeout=25) as reponse:
        return reponse.read()


def lire_pdf(contenu):
    lecteur = PdfReader(BytesIO(contenu))
    textes = []
    for page in lecteur.pages[:15]:
        textes.append(page.extract_text() or "")
    return "\n".join(textes)


def nettoyer_html(contenu):
    texte = contenu.decode("utf-8", errors="replace")
    texte = re.sub(
        r"(?is)<(script|style)\b[^>]*>.*?</\1>",
        " ", texte
    )
    texte = re.sub(r"<[^>]+>", " ", texte)
    return unescape(re.sub(r"\s+", " ", texte))


def extraire_liens_pdf(contenu, url_base):
    html = contenu.decode("utf-8", errors="replace")
    liens = re.findall(
        r"""href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']""",
        html, re.IGNORECASE
    )
    return {
        urljoin(url_base, unescape(lien))
        for lien in liens
    }


def enregistrer_observations(nom, url, texte, observations, connus):
    texte = re.sub(r"\s+", " ", texte)
    for mot in MOTS_CLES:
        for correspondance in re.finditer(
            re.escape(mot), texte, re.IGNORECASE
        ):
            debut = max(0, correspondance.start() - 100)
            fin = min(len(texte), correspondance.end() + 160)
            extrait = texte[debut:fin].strip()
            identifiant = nom + extrait

            if identifiant not in connus:
                observations.append({
                    "source": nom,
                    "url": url,
                    "extrait": extrait,
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
    item.get("source", "") + item.get("extrait", "")
    for item in observations
}

pdf_a_consulter = dict(PDF_CONNUS)

for nom, url in SOURCES.items():
    try:
        contenu = telecharger(url)
        texte = nettoyer_html(contenu)

        enregistrer_observations(
            nom, url, texte, observations, connus
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
        enregistrer_observations(
            nom, url, texte, observations, connus
        )
        print(f"PDF consulte : {url}")

    except Exception as erreur:
        print(f"Erreur PDF {url} : {erreur}")

FICHIER.write_text(
    json.dumps(
        {"observations": observations},
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

print(f"Total observations : {len(observations)}")

