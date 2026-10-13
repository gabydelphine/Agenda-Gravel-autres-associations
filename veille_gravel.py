import json
import re
import unicodedata
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
    "Circuit Gravel Saubens 2026": "https://www.cyclotourisme-31.com/uploaded/Circuits_InterClubs/2026/saubensgravel2026.pdf",
}
FICHIER = Path("veille-resultats.json")
MOIS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12}
MAX_PDF = 10_000_000
MAX_PDF_PAR_SITE = 25


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in texte if not unicodedata.combining(c)).casefold()


def telecharger(url):
    requete = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; GravelAgenda/1.0)"})
    with urlopen(requete, timeout=25) as reponse:
        return reponse.read(MAX_PDF + 1)


def nettoyer_html(contenu):
    html = contenu.decode("utf-8", errors="replace")
    html = re.sub(r"(?is)<(script|style|nav|footer)\b[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)</(?:div|article|li|p|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    return unescape(html)


def lire_pdf(contenu):
    lecteur = PdfReader(BytesIO(contenu))
    return "\n".join(page.extract_text() or "" for page in lecteur.pages[:15])


def extraire_liens_pdf(contenu, base):
    html = contenu.decode("utf-8", errors="replace")
    liens = re.findall(r'''href\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']''', html, re.I)
    return {urljoin(base, unescape(lien)) for lien in liens}


def trouver_date(texte):
    texte = normaliser(texte)
    motif = r"\b(\d{1,2})\s+(" + "|".join(MOIS) + r")\s+(20\d{2})\b"
    match = re.search(motif, texte)
    if match:
        jour, mois, annee = match.groups()
        try:
            return datetime(int(annee), MOIS[mois], int(jour)).date().isoformat()
        except ValueError:
            return None
    return None


def extraire_lieu(bloc):
    match = re.search(
        r"(?:\d{1,2}:\d{2}\s*(?:am|pm)\s*){2}(.+?)\s+(?:VTT|Gravel|Route|Marche)\b",
        bloc, re.I,
    )
    if not match:
        return None
    lieu = re.sub(r"\s+", " ", match.group(1)).strip()
    if not 2 <= len(lieu) <= 60 or re.search(r"\b(?:parcours|details|connexion|inscription)\b", normaliser(lieu)):
        return None
    return lieu


def enregistrer(nom, url, extrait, observations, connus, lieu=None):
    extrait = re.sub(r"\s+", " ", extrait).strip()[:500]
    if not re.search(r"\bgravel\b", extrait, re.I) or len(extrait) < 30:
        return
    identifiant = url + "|" + extrait.casefold()
    if identifiant in connus:
        if lieu:
            for observation in observations:
                if (observation.get("url", "") + "|" + observation.get("extrait", "").casefold() == identifiant
                        and not observation.get("lieu")):
                    observation["lieu"] = lieu
        return
    observation = {
        "source": nom, "url": url, "extrait": extrait,
        "date_evenement": trouver_date(extrait),
        "date_detection": datetime.now(timezone.utc).date().isoformat(),
        "statut": "a_verifier",
    }
    if lieu:
        observation["lieu"] = lieu
    observations.append(observation)
    connus.add(identifiant)
    print(f"Nouvelle observation : {nom}")


if FICHIER.exists():
    donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
else:
    donnees = {"observations": []}
observations = donnees.get("observations", [])
connus = {item.get("url", "") + "|" + item.get("extrait", "").casefold() for item in observations}
pdf_a_consulter = dict(PDF_CONNUS)

for nom, url in SOURCES.items():
    try:
        contenu = telecharger(url)
        texte = nettoyer_html(contenu)
        if nom == "Velo Occitanie":
            texte_normalise = re.sub(r"\s+", " ", texte)
            motif_date = r"\b(?:Lun|Mar|Mer|Jeu|Ven|Sam|Dim)\.?\s+\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2}\b"
            dates = list(re.finditer(motif_date, texte_normalise, re.I))
            for i, match in enumerate(dates):
                fin = dates[i + 1].start() if i + 1 < len(dates) else len(texte_normalise)
                bloc = re.split(r"\bDétails\b", texte_normalise[match.end():fin], maxsplit=1, flags=re.I)[0]
                if not re.search(r"\bGravel\b", bloc, re.I):
                    continue
                avant = texte_normalise[max(0, match.start() - 120):match.start()]
                titre = re.split(r"\bDétails\b", avant, flags=re.I)[-1].strip()
                if not titre or len(titre) > 100 or not trouver_date(match.group()):
                    continue
                extrait = f"Détails {titre} {match.group()} {bloc}"
                enregistrer(nom, url, extrait, observations, connus, lieu=extraire_lieu(bloc))
                print(f"Gravel détecté : {titre}")
        # Collecter les PDF liés depuis chaque page source, sans suivre de liens externes.
        liens = sorted(extraire_liens_pdf(contenu, url))
        ajoutes = 0
        for lien in liens:
            if urlparse(lien).netloc.lower() != urlparse(url).netloc.lower():
                continue
            if lien not in pdf_a_consulter:
                pdf_a_consulter[lien] = lien
                ajoutes += 1
            if ajoutes >= MAX_PDF_PAR_SITE:
                break
        print(f"Site consulté : {nom} ({ajoutes} nouveaux PDF)")
    except Exception as erreur:
        print(f"Erreur site {nom} : {erreur}")

for nom, url in pdf_a_consulter.items():
    try:
        contenu = telecharger(url)
        if len(contenu) > MAX_PDF:
            print(f"PDF trop volumineux : {url}")
            continue
        texte = lire_pdf(contenu)
        lignes = [re.sub(r"\s+", " ", ligne).strip() for ligne in texte.splitlines()]
        for i, ligne in enumerate(lignes):
            if re.search(r"\bgravel\b", ligne, re.I):
                extrait = " ".join(lignes[max(0, i - 3):i + 4])
                enregistrer(nom, url, extrait, observations, connus)
        print(f"PDF consulté : {url}")
    except Exception as erreur:
        print(f"Erreur PDF {url} : {erreur}")

FICHIER.write_text(json.dumps({"observations": observations}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Total observations : {len(observations)}")
