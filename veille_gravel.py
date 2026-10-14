import json
import math
import re
import unicodedata
from collections import deque
from datetime import datetime, timezone, date
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, urlencode
from urllib.request import Request, urlopen

from pypdf import PdfReader

SOURCES = {
    "FSGT 31": "https://cyclismefsgt31.fr/",
    "FFVelo 31": "https://haute-garonne.ffvelo.fr/",
    "Cyclotourisme 31": "https://www.cyclotourisme-31.com/",
    "FFVelo 82": "https://cyclotourisme82-ffct.fr/",
    "Velo Occitanie": "https://www.velo-occitanie.fr/",
    "Saubens Cyclo": "https://saubenscyclo31.fr/",
    "Battistrada Gravel": "https://battistrada.com/fr/calendrier-cyclosportives/gravel/?biketype=4",
    "Gravel Agenda": "https://gravelagenda.com/",
    "Gravel Passion": "https://www.gravelpassion.fr/evenements-calendrier-gravel/",
    "Fontenilles VTT": "https://fontenilles-vtt.fr/",
}

BATTISTRADA_EVENEMENTS = (
    "https://battistrada.com/fr/calendrier-cyclosportives/edition/rando-nocturne-la-revdautan-2026/59674/",
)

# Pages événementielles ciblées (elles complètent les sites généraux).
PAGES_CIBLEES = {
    "VTT31 / Vélo Occitanie": [
        "https://velo-occitanie.fr/vtt-31/randonnee-vtt-gravel-2026",
        "https://velo-occitanie.fr/vtt-31/39eme-randonnee-de-venerque-31-2026",
        "https://www.velo-occitanie.fr/vtt/evenements-vtt-haute-garonne-31/vtt-31/5eme-nocturne-du-lys-2025",
    ],
    "Fontenilles VTT": [
        "https://fontenilles-vtt.fr/wp/?page_id=4553",
    ],
}
# Facebook VTT31 : lien exact non encore confirmé. Ne pas simuler
# un accès automatique à des publications Facebook non publiques.
FACEBOOK_VTT31 = None

PDF_CONNUS = {
    "Circuit Gravel Saubens 2026": (
        "https://www.cyclotourisme-31.com/"
        "uploaded/Circuits_InterClubs/2026/saubensgravel2026.pdf"
    ),
}

FICHIER = Path("veille-resultats.json")

# Distance à vol d'oiseau, et non distance routière.
RAYON_KM = 150
TOULOUSE_LAT, TOULOUSE_LON = 43.6047, 1.4442
_cache_communes = {}


def distance_km(lat, lon):
    """Distance à vol d'oiseau entre Toulouse et une commune."""
    r = 6371.0088
    p1, p2 = math.radians(TOULOUSE_LAT), math.radians(lat)
    dp = p2 - p1
    dl = math.radians(lon - TOULOUSE_LON)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(min(1, math.sqrt(a))), 1)


def verifier_commune(lieu):
    """Renvoie (distance, état). En cas de doute, ne rejette jamais l'annonce.

    La base publique des communes est interrogée seulement pour les lieux explicites.
    Un nom de commune ambigu n'est pas utilisé pour exclure un événement.
    """
    if not lieu:
        return None, "lieu_a_verifier"
    lieu = re.sub(r"\s+", " ", lieu).strip()
    match_dept = re.search(r"\((\d{2,3}[AB]?)\)\s*$", lieu, re.I)
    departement = match_dept.group(1).upper() if match_dept else None
    nom = re.sub(r"\s*\(\d{2,3}[AB]?\)\s*$", "", lieu, flags=re.I).strip()
    if not nom or len(nom) > 65:
        return None, "lieu_a_verifier"
    cle = (normaliser(nom), departement)
    if cle in _cache_communes:
        return _cache_communes[cle]
    try:
        params = urlencode({"nom": nom, "fields": "nom,codeDepartement,centre", "format": "json", "geometry": "centre", "limit": 30})
        contenu, _ = telecharger("https://geo.api.gouv.fr/communes?" + params, 250_000)
        resultats = json.loads(contenu)
        exacts = [c for c in resultats if normaliser(c.get("nom")) == normaliser(nom)
                  and (not departement or c.get("codeDepartement") == departement)]
        if len(exacts) != 1:
            resultat = (None, "lieu_ambigu_ou_inconnu")
        else:
            coord = exacts[0].get("centre", {}).get("coordinates", [])
            if len(coord) != 2:
                resultat = (None, "coordonnees_indisponibles")
            else:
                distance = distance_km(coord[1], coord[0])
                resultat = (distance, "dans_zone" if distance <= RAYON_KM else "hors_zone")
    except Exception as erreur:
        print(f"Vérification géographique impossible pour {lieu}: {erreur}")
        resultat = (None, "geolocalisation_indisponible")
    _cache_communes[cle] = resultat
    return resultat

MOIS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
}

MAX_PDF = 10_000_000
MAX_PDF_PAR_SITE = 25
MAX_PAGES_PAR_SITE = 15
MAX_PROFONDEUR = 2
MAX_OCTETS_PAGE = 2_000_000

MOTS_PAGES_PRIORITAIRES = (
    "agenda", "calendrier", "evenement", "manifestation", "randonnee",
    "gravel", "circuit", "sortie", "interclubs", "cyclotourisme",
)

MOTS_DOCUMENTS_A_EXCLURE = (
    "assemblee generale", "rapport d activite", "rapport d'activité",
    "participations aux", "tableau des medailles", "tableau des médailles",
    "reglement", "règlement", "challenge", "saison 2023", "saison 2024",
    "saison 2025", "bilan", "historique de mes inscriptions",
    "informations historiques", "statistiques",
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
    html = re.sub(
        r"(?i)</(?:div|article|li|p|h[1-6]|tr|section)>", "\n", html
    )
    html = re.sub(r"<[^>]+>", " ", html)
    return unescape(html)


def extraire_liens(contenu, base):
    html = contenu.decode("utf-8", errors="replace")
    liens = re.findall(r"""(?is)\bhref\s*=\s*["']([^"']+)["']""", html)
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


def lire_pdf(contenu):
    lecteur = PdfReader(BytesIO(contenu))
    return "\n".join(page.extract_text() or "" for page in lecteur.pages[:15])


def trouver_date(texte):
    texte_n = normaliser(texte)
    motif = r"\b(\d{1,2})\s+(" + "|".join(MOIS) + r")\s+(20\d{2})\b"
    match = re.search(motif, texte_n)
    if not match:
        return None
    jour, mois, annee = match.groups()
    try:
        return date(int(annee), MOIS[mois], int(jour)).isoformat()
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
    if not 2 <= len(lieu) <= 60:
        return None
    if re.search(r"\b(?:parcours|details|connexion|inscription)\b", normaliser(lieu)):
        return None
    return lieu


def extrait_a_exclure(extrait):
    texte = normaliser(extrait)
    return any(normaliser(mot) in texte for mot in MOTS_DOCUMENTS_A_EXCLURE)


def est_candidat_evenement(extrait, date_evenement=None, pdf_connu=False):
    texte = normaliser(extrait)
    if not re.search(r"\bgravel\b", texte):
        return False
    if extrait_a_exclure(extrait):
        return False
    if pdf_connu and "saubenoise" in texte:
        return True
    if not date_evenement:
        return False
    try:
        jour = date.fromisoformat(date_evenement)
    except (TypeError, ValueError):
        return False
    if jour < date.today():
        return False
    return True


def cle_observation(url, extrait):
    return url + "|" + normaliser(extrait)


def cle_evenement_approximative(url, extrait):
    texte = normaliser(extrait)
    titre_match = re.search(
        r"(?:details\s+)?(.{3,90}?)\s+"
        r"(?:dim|lun|mar|mer|jeu|ven|sam)\.?\s+\d{1,2}\s+"
        r"[a-z]+\s+20\d{2}",
        texte,
    )
    date_evenement = trouver_date(extrait)
    if titre_match and date_evenement:
        titre = titre_match.group(1).strip(" -:")
        lieu_match = re.search(
            r"\b([a-z][a-z '\-]{2,45})\s+(?:vtt|gravel|route|marche)\b",
            texte,
        )
        lieu = lieu_match.group(1).strip() if lieu_match else ""
        return f"event|{date_evenement}|{titre}|{lieu}"
    return cle_observation(url, extrait)


def enregistrer(nom, url, extrait, observations, connus, lieux_connus,
                lieu=None, pdf_connu=False):
    extrait = re.sub(r"\s+", " ", extrait or "").strip()[:500]
    if len(extrait) < 30:
        return
    date_evenement = trouver_date(extrait)
    if not est_candidat_evenement(extrait, date_evenement, pdf_connu=pdf_connu):
        return

    identifiant = cle_observation(url, extrait)
    cle_event = cle_evenement_approximative(url, extrait)
    if identifiant in connus or cle_event in lieux_connus:
        if lieu:
            for observation in observations:
                if cle_observation(
                    observation.get("url", ""), observation.get("extrait", "")
                ) == identifiant and not observation.get("lieu"):
                    observation["lieu"] = lieu
        return

    distance, controle_zone = verifier_commune(lieu)
    # Ne pas supprimer silencieusement les événements hors zone : ils restent
    # dans la revue humaine, signalés explicitement.
    observation = {
        "rayon_recherche_km": RAYON_KM,
        "controle_zone": controle_zone,
        "source": nom,
        "url": url,
        "extrait": extrait,
        "date_evenement": date_evenement,
        "date_detection": datetime.now(timezone.utc).date().isoformat(),
        "statut": "a_verifier",
    }
    if lieu:
        observation["lieu"] = lieu
    if distance is not None:
        observation["distance_toulouse_km"] = distance
    observations.append(observation)
    connus.add(identifiant)
    lieux_connus.add(cle_event)
    print(f"Nouvelle observation : {nom} — {extrait[:120]}")


def analyser_velo_occitanie(texte, url, observations, connus, lieux_connus):
    texte_normalise = re.sub(r"\s+", " ", texte)
    motif_date = (
        r"\b(?:Lun|Mar|Mer|Jeu|Ven|Sam|Dim)\.?\s+"
        r"\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2}\b"
    )
    dates = list(re.finditer(motif_date, texte_normalise, re.I))
    for i, match in enumerate(dates):
        fin = dates[i + 1].start() if i + 1 < len(dates) else len(texte_normalise)
        bloc = re.split(
            r"\bDétails\b", texte_normalise[match.end():fin],
            maxsplit=1, flags=re.I
        )[0]
        if not re.search(r"\bGravel\b", bloc, re.I):
            continue
        avant = texte_normalise[max(0, match.start() - 120):match.start()]
        titre = re.split(r"\bDétails\b", avant, flags=re.I)[-1].strip()
        if not titre or len(titre) > 100:
            continue
        extrait = f"Détails {titre} {match.group()} {bloc}"
        enregistrer(
            "Velo Occitanie", url, extrait, observations, connus,
            lieux_connus, lieu=extraire_lieu(bloc),
        )
        print(f"Annonce Gravel examinée : {titre}")


def analyser_battistrada(url, observations, connus, lieux_connus):
    """Collecte prudente : candidats gravel à vérifier manuellement."""
    try:
        contenu, _ = telecharger(url, MAX_OCTETS_PAGE)
        texte = nettoyer_html(contenu)
        # Les fiches peuvent être en français ou présenter une date numérique.
        # On conserve uniquement les dates explicites au format déjà reconnu.
        date_evenement = trouver_date(texte)
        if not date_evenement or date.fromisoformat(date_evenement) < date.today():
            print(f"Battistrada : date absente ou passée : {url}")
            return
        if not re.search(r"\bgravel\b", normaliser(texte)):
            print(f"Battistrada : parcours gravel non confirmé : {url}")
            return
        # Limiter le texte à un extrait court et contextualisé.
        titre = re.search(r"(?is)<title[^>]*>(.*?)</title>", contenu.decode("utf-8", errors="replace"))
        titre = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", titre.group(1)))).strip() if titre else "Événement Battistrada"
        extrait = f"{titre} — {date_evenement} — Gravel. " + re.sub(r"\s+", " ", texte)[:250]
        # enregistrer() attend une date en toutes lettres, et garde le statut à vérifier.
        jour = date.fromisoformat(date_evenement)
        extrait = f"{titre} — {jour.day} {list(MOIS)[jour.month-1]} {jour.year} — Gravel. " + re.sub(r"\s+", " ", texte)[:200]
        enregistrer("Battistrada", url, extrait, observations, connus, lieux_connus)
    except Exception as erreur:
        print(f"Erreur Battistrada {url} : {erreur}")



def analyser_page_ciblee(nom, url, observations, connus, lieux_connus):
    """Recherche une date gravel sur une fiche ciblée, sans publication automatique."""
    try:
        contenu, _ = telecharger(url, MAX_OCTETS_PAGE)
        html = contenu.decode("utf-8", errors="replace")
        texte = nettoyer_html(contenu)
        if not re.search(r"\bgravel\b", normaliser(texte)):
            print(f"Page sans gravel explicite : {url}")
            return
        titre_match = re.search(r"(?is)<h1\b[^>]*>(.*?)</h1>", html)
        if not titre_match:
            titre_match = re.search(r"(?is)<title\b[^>]*>(.*?)</title>", html)
        titre = nettoyer_html(titre_match.group(1).encode()) if titre_match else nom
        titre = re.sub(r"\s+", " ", titre).strip()[:120]
        # Plusieurs dates peuvent apparaître dans les menus / articles liés :
        # la première date n'est qu'une observation à contrôler.
        date_evenement = trouver_date(texte)
        if not date_evenement:
            m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d{2})\b", texte)
            if m:
                try:
                    date_evenement = date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
                except ValueError:
                    pass
        if not date_evenement:
            print(f"Date non déterminée : {url}")
            return
        jour = date.fromisoformat(date_evenement)
        if jour < date.today() - __import__('datetime').timedelta(days=365):
            return
        extrait = f"{titre} — {jour.day} {list(MOIS)[jour.month-1]} {jour.year} — Gravel. " + re.sub(r"\s+", " ", texte)[:200]
        # Les événements passés récents sont conservés pour la revue historique.
        if jour < date.today():
            identifiant = cle_observation(url, extrait)
            if identifiant not in connus:
                observations.append({"rayon_recherche_km": RAYON_KM,
                                     "controle_zone": "lieu_a_verifier",
                                     "source": nom, "url": url, "extrait": extrait[:500],
                                     "date_evenement": date_evenement,
                                     "date_detection": datetime.now(timezone.utc).date().isoformat(),
                                     "statut": "a_verifier"})
                connus.add(identifiant)
        else:
            enregistrer(nom, url, extrait, observations, connus, lieux_connus)
        print(f"Fiche examinée : {nom} — {titre}")
    except Exception as erreur:
        print(f"Erreur fiche ciblée {url} : {erreur}")

def explorer_site(nom, url, pdf_a_consulter):
    hote = urlparse(url).netloc.lower()
    file_attente = deque([(url, 0)])
    pages_vues = set()
    nb_pdf = 0

    while (
        file_attente
        and len(pages_vues) < MAX_PAGES_PAR_SITE
        and nb_pdf < MAX_PDF_PAR_SITE
    ):
        page, profondeur = file_attente.popleft()
        page = urldefrag(page)[0]
        if page in pages_vues or urlparse(page).netloc.lower() != hote or est_pdf(page):
            continue
        pages_vues.add(page)
        try:
            contenu, type_contenu = telecharger(page, MAX_OCTETS_PAGE)
            if "pdf" in type_contenu:
                pdf_a_consulter.setdefault(page, (nom, page))
                nb_pdf += 1
                continue
            if type_contenu and not any(
                t in type_contenu for t in ("html", "text/", "application/xhtml")
            ):
                continue
            liens = extraire_liens(contenu, page)
            pdf_liens = sorted(
                lien for lien in liens
                if est_pdf(lien) and urlparse(lien).netloc.lower() == hote
            )
            for lien in pdf_liens:
                if lien not in pdf_a_consulter and nb_pdf < MAX_PDF_PAR_SITE:
                    pdf_a_consulter[lien] = (nom, lien)
                    nb_pdf += 1
            if profondeur < MAX_PROFONDEUR and len(pages_vues) < MAX_PAGES_PAR_SITE:
                internes = [
                    lien for lien in liens
                    if urlparse(lien).netloc.lower() == hote and not est_pdf(lien)
                ]
                internes.sort(key=lambda lien: (
                    0 if any(mot in normaliser(lien) for mot in MOTS_PAGES_PRIORITAIRES) else 1,
                    len(lien),
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
    cle_observation(item.get("url", ""), item.get("extrait", ""))
    for item in observations
}
lieux_connus = {
    cle_evenement_approximative(item.get("url", ""), item.get("extrait", ""))
    for item in observations
    if item.get("date_evenement")
}
pdf_a_consulter = {
    url: (nom, url) for nom, url in PDF_CONNUS.items()
}

for nom, url in SOURCES.items():
    try:
        contenu, _ = telecharger(url, MAX_OCTETS_PAGE)
        texte = nettoyer_html(contenu)
        if nom == "Velo Occitanie":
            analyser_velo_occitanie(
                texte, url, observations, connus, lieux_connus
            )
        explorer_site(nom, url, pdf_a_consulter)
    except Exception as erreur:
        print(f"Erreur site {nom} : {erreur}")

# Examiner la fiche signalée et les fiches d'événements reliées au calendrier.
for nom, urls in PAGES_CIBLEES.items():
    for url in urls:
        analyser_page_ciblee(nom, url, observations, connus, lieux_connus)

try:
    listing_url = SOURCES["Battistrada Gravel"]
    contenu, _ = telecharger(listing_url, MAX_OCTETS_PAGE)
    fiches = {lien for lien in extraire_liens(contenu, listing_url)
              if "battistrada.com" in urlparse(lien).netloc
              and "/edition/" in urlparse(lien).path}
    fiches.update(BATTISTRADA_EVENEMENTS)
    for fiche in sorted(fiches)[:80]:
        analyser_battistrada(fiche, observations, connus, lieux_connus)
except Exception as erreur:
    print(f"Erreur calendrier Battistrada : {erreur}")
    for fiche in BATTISTRADA_EVENEMENTS:
        analyser_battistrada(fiche, observations, connus, lieux_connus)

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
        pdf_connu = url in PDF_CONNUS.values()
        for i, ligne in enumerate(lignes):
            if not re.search(r"\bgravel\b", ligne, re.I):
                continue
            extrait = " ".join(lignes[max(0, i - 3):i + 4])
            enregistrer(
                nom, url, extrait, observations, connus, lieux_connus,
                pdf_connu=pdf_connu,
            )
        print(f"PDF consulté : {url}")
    except Exception as erreur:
        print(f"Erreur PDF {url} : {erreur}")

FICHIER.write_text(
    json.dumps({"observations": observations}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"Total observations conservées : {len(observations)}")
