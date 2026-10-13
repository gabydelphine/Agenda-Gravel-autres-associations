import json
import re
import unicodedata
from collections import deque
from datetime import datetime, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import Request, urlopen

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
    "Circuit Gravel Saubens 2026": (
        "https://www.cyclotourisme-31.com/"
        "uploaded/Circuits_InterClubs/2026/saubensgravel2026.pdf"
    ),
}

FICHIER = Path("veille-resultats.json")
MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}
MAX_PDF = 10_000_000
MAX_PDF_PAR_SITE = 25
MAX_PAGES_PAR_SITE = 15
MAX_PROFONDEUR = 2
MOTS_PAGES_PRIORITAIRES = (
    "agenda", "calendrier", "evenement", "manifestation", "randonnee",
    "randonnée", "gravel", "circuit", "sortie", "interclubs", "cyclotourisme",
)


def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texte).strip().casefold()


def telecharger(url, limite=MAX_PDF + 1):
    requete = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; GravelAgenda/1.0)"},
    )
    with urlopen(requete, timeout=25) as reponse:
        contenu = reponse.read(limite)
        type_contenu = reponse.headers.get("Content-Type", "").lower()
    return contenu, type_contenu


def nettoyer_html(contenu):
    html = contenu.decode("utf-8", errors="replace")
    html = re.sub(
        r"(?is)<(script|style|nav|footer|header)\b[^>]*>.*?</\1>",
        " ", html,
    )
    html = re.sub(r"(?i)</(?:div|article|li|p|h[1-6]|tr|section)>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    return unescape(html)


def extraire_liens(contenu, base):
    html = contenu.decode("utf-8", errors="replace")
    liens = re.findall(r'''(?is)\bhref\s*=\s*["']([^"']+)["']''', html)
    resultats = set()
    for lien in liens:
        lien = unescape(lien.strip())
        if not lien or lien.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolu = urldefrag(urljoin(base, lien))[0]
        if urlparse(absolu).scheme in ("http", "https"):
            resultats.add(absolu)
    return resultats


def est_pdf(url):
    chemin = urlparse(url).path.casefold()
    return chemin.endswith(".pdf") or ".pdf/" in chemin


def meme_hote(url, base):
    return urlparse(url).netloc.lower() == urlparse(base).netloc.lower()


def lire_pdf(contenu):
    lecteur = PdfReader(BytesIO(contenu))
    return "\n".join(page.extract_text() or "" for page in lecteur.pages[:15])


def trouver_date(texte):
    texte = normaliser(texte)
    motif = r"\b(\d{1,2})\s+(" + "|".join(MOIS) + r")\s+(20\d{2})\b"
    match = re.search(motif, texte)
    if not match:
        return None
    jour, mois, annee = match.groups()
    try:
        return datetime(int(annee), MOIS[mois], int(jour)).date().isoformat()
    except ValueError:
        return None


def extraire_lieu(bloc):
    match = re.search(
        r"(?:\d{1,2}:\d{2}\s*(?:am|pm)\s*){2}(.+?)\s+(?:VTT|Gravel|Route|Marche)\b",
        bloc, re.I,
    )
    if not match:
        return None
    lieu = re.sub(r"\s+", " ", match.group(1)).strip()
    if not 2 <= len(lieu) <= 60 or re.search(
        r"\b(?:parcours|details|connexion|inscription)\b", normaliser(lieu)
    ):
        return None
    return lieu


def enregistrer(nom, url, extrait, observations, connus, lieu=None):
    extrait = re.sub(r"\s+", " ", extrait or "").strip()[:500]
    if not re.search(r"\bgravel\b", extrait, re.I) or len(extrait) < 30:
        return

    identifiant = url + "|" + normaliser(extrait)
    if identifiant in connus:
        if lieu:
            for observation in observations:
                cle = observation.get("url", "") + "|" + normaliser(observation.get("extrait", ""))
                if cle == identifiant and not observation.get("lieu"):
                    observation["lieu"] = lieu
        return

    observation = {
        "source": nom,
        "url": url,
        "extrait": extrait,
        "date_evenement": trouver_date(extrait),
        "date_detection": datetime.now(timezone.utc).date().isoformat(),
        "statut": "a_verifier",
    }
    if lieu:
        observation["lieu"] = lieu
    observations.append(observation)
    connus.add(identifiant)
    print(f"Nouvelle observation : {nom} — {extrait[:100]}")


def analyser_velo_occitanie(texte, url, observations, connus):
    texte_normalise = re.sub(r"\s+", " ", texte)
    motif_date = (
        r"\b(?:Lun|Mar|Mer|Jeu|Ven|Sam|Dim)\.?\s+"
        r"\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2}\b"
    )
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
        enregistrer("Velo Occitanie", url, extrait, observations, connus, lieu=extraire_lieu(bloc))
        print(f"Gravel détecté : {titre}")


def explorer_site(nom, url, pdf_a_consulter):
    """Explore quelques pages internes, en privilégiant agenda, calendrier et circuits."""
    hote = urlparse(url).netloc.lower()
    file_attente = deque([(url, 0)])
    pages_vues = set()
    nb_pdf = 0

    while file_attente and len(pages_vues) < MAX_PAGES_PAR_SITE and nb_pdf < MAX_PDF_PAR_SITE:
        page, profondeur = file_attente.popleft()
        page = urldefrag(page)[0]
        if page in pages_vues or urlparse(page).netloc.lower() != hote or est_pdf(page):
            continue
        pages_vues.add(page)
        try:
            contenu, type_contenu = telecharger(page, 2_000_000)
            if "pdf" in type_contenu:
                pdf_a_consulter.setdefault(page, (nom, page))
                nb_pdf += 1
                continue
            if type_contenu and not any(t in type_contenu for t in ("html", "text/", "application/xhtml")):
                continue
            liens = extraire_liens(contenu, page)
            pdf_liens = sorted(l for l in liens if est_pdf(l) and urlparse(l).netloc.lower() == hote)
            for lien in pdf_liens:
                if lien not in pdf_a_consulter and nb_pdf < MAX_PDF_PAR_SITE:
                    pdf_a_consulter[lien] = (nom, lien)
                    nb_pdf += 1

            if profondeur < MAX_PROFONDEUR and len(pages_vues) < MAX_PAGES_PAR_SITE:
                internes = [l for l in liens if urlparse(l).netloc.lower() == hote and not est_pdf(l)]
                internes.sort(key=lambda l: (
                    0 if any(mot in normaliser(l) for mot in MOTS_PAGES_PRIORITAIRES) else 1,
                    len(l),
                ))
                for lien in internes:
                    if lien not in pages_vues:
                        file_attente.append((lien, profondeur + 1))
        except Exception as erreur:
            print(f"Erreur page {page} : {erreur}")

    print(f"Site consulté : {nom} ({len(pages_vues)} pages, {nb_pdf} PDF repérés)")


if FICHIER.exists():
    donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
else:
    donnees = {"observations": []}

observations = donnees.get("observations", [])
connus = {
    item.get("url", "") + "|" + normaliser(item.get("extrait", ""))
    for item in observations
}
pdf_a_consulter = {url: (nom, url) for nom, url in PDF_CONNUS.items()}

# Première exploration : pages des six sources et liens PDF trouvés dans leurs pages internes.
for nom, url in SOURCES.items():
    try:
        contenu, _ = telecharger(url, 2_000_000)
        texte = nettoyer_html(contenu)
        if nom == "Velo Occitanie":
            analyser_velo_occitanie(texte, url, observations, connus)
        explorer_site(nom, url, pdf_a_consulter)
    except Exception as erreur:
        print(f"Erreur site {nom} : {erreur}")

# Lecture des PDF repérés ; les PDF existants dans l'historique sont conservés.
for url, (nom, _) in pdf_a_consulter.items():
    try:
        contenu, type_contenu = telecharger(url)
        if len(contenu) > MAX_PDF:
            print(f"PDF trop volumineux : {url}")
            continue
        if not (contenu.startswith(b"%PDF") or "pdf" in type_contenu or est_pdf(url)):
            print(f"Lien ignoré (pas un PDF) : {url}")
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

FICHIER.write_text(
    json.dumps({"observations": observations}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"Total observations : {len(observations)}")
