#!/usr/bin/env python3
"""Veille automatique des manifestations gravel autour de Toulouse.

Principe (version 5) :
  1. Les sites à surveiller sont décrits dans sources.json (aucun événement
     n'est écrit dans ce programme).
  2. Une recherche web quotidienne (Brave Search, clé BRAVE_API_KEY) découvre
     les pages que personne n'a signalées.
  3. Chaque page est lue : données structurées (schema.org Event, iCal) en
     priorité, sinon lecture du texte ligne par ligne.
  4. Le lieu est reconnu à partir de la base officielle des communes
     (geo.api.gouv.fr), mise en cache dans cache/communes-zone.json.
  5. Rien n'est publié : les nouveautés entrent dans inventaire-gravel.json
     avec le statut « a_verifier » et partent dans l'e-mail du dimanche.
     Les annonces gravel datées dont le lieu n'est pas reconnu restent dans
     veille-resultats.json (rubrique « a_examiner ») au lieu d'être jetées.
"""
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import (quote, unquote, urldefrag, urlencode, urljoin,
                          urlparse, urlsplit, urlunsplit)
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

CONFIG = Path("sources.json")
INVENTAIRE = Path("inventaire-gravel.json")
CALENDRIER = Path("events.json")
RESULTATS = Path("veille-resultats.json")
CACHE_COMMUNES = Path("cache/communes-zone.json")

AUJOURDHUI = date.today()
UA = "Mozilla/5.0 (compatible; VeilleGravelToulouse/5.0; +https://github.com/gabydelphine/Agenda-Gravel-autres-associations)"
MAX_OCTETS_PAGE = 3_000_000
MAX_OCTETS_PDF = 10_000_000

MOIS = {
    "janvier": 1, "janv": 1, "fevrier": 2, "fevr": 2, "fev": 2, "mars": 3,
    "avril": 4, "avr": 4, "mai": 5, "juin": 6, "juillet": 7, "juil": 7,
    "aout": 8, "septembre": 9, "sept": 9, "octobre": 10, "oct": 10,
    "novembre": 11, "nov": 11, "decembre": 12, "dec": 12,
}
NOMS_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]

# Mots qui sont aussi des noms de communes mais ne désignent presque jamais
# un lieu dans une annonce : on ne les accepte qu'avec un code postal ou (dd).
MOTS_AMBIGUS = {
    "gravel", "vtt", "route", "marche", "club", "velo", "rando", "sport",
    "depart", "salle", "lac", "foret", "bois", "ville", "centre", "eglise",
    "stade", "mairie", "prix", "place", "mur", "mas", "bas", "port", "mont",
    "pont", "pin", "sol", "fa", "ger", "lys", "bon", "beaucoup", "vers",
    "orange", "plan", "sorties", "fontaine", "moulin", "castel", "auch",
    "la salle", "les cabanes", "le pin", "le vernet", "la croix",
}

MOTIF_GRAVEL = re.compile(r"\bgravels?\b|\bgravier\b")


# ---------------------------------------------------------------------------
# Outils généraux
# ---------------------------------------------------------------------------

def normaliser(texte):
    texte = unicodedata.normalize("NFKD", str(texte or ""))
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texte).strip().casefold()


def cle_nom(texte):
    """Clé de comparaison des communes : « St-Lys » et « Saint Lys » → « saint lys »."""
    t = re.sub(r"[^a-z0-9]+", " ", normaliser(texte)).strip()
    t = re.sub(r"\bste\b", "sainte", t)
    t = re.sub(r"\bst\b", "saint", t)
    return t


def cle_titre(texte):
    t = cle_nom(texte)
    t = re.sub(r"\b(?:\d+(?:e|eme|er|ere)?|edition|la|le|les|de|du|des|d|l|a|en|et|randonnee|rando|gravel|vtt|20\d\d)\b", " ", t)
    return re.sub(r"\s+", "", t)


def charger_json(chemin, defaut):
    if not chemin.exists():
        return defaut
    return json.loads(chemin.read_text(encoding="utf-8"))


def ecrire_json(chemin, donnees):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def distance_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return round(2 * r * math.asin(min(1, math.sqrt(a))), 1)


def journal(message):
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Téléchargement poli (un délai par site, compteur global)
# ---------------------------------------------------------------------------

class Telechargeur:
    def __init__(self, delai, maximum):
        self.delai = delai
        self.maximum = maximum
        self.dernier = {}
        self.nombre = 0
        self.erreurs = 0
        self.robots = {}

    def autorise(self, url):
        """Respecte le fichier robots.txt de chaque site."""
        p = urlparse(url)
        racine = f"{p.scheme}://{p.netloc}"
        if racine not in self.robots:
            rp = RobotFileParser()
            try:
                contenu, _, _ = self.get(racine + "/robots.txt", 500_000, compter=False, verifier=False)
                rp.parse(decoder(contenu).splitlines())
            except Exception:
                rp = None  # pas de robots.txt lisible : accès autorisé
            self.robots[racine] = rp
        rp = self.robots[racine]
        return rp is None or rp.can_fetch("VeilleGravelToulouse", url)

    @staticmethod
    def url_valide(url):
        m = urlsplit(url)
        if m.scheme not in ("http", "https"):
            raise ValueError("URL non HTTP(S)")
        return urlunsplit((m.scheme, m.netloc,
                           quote(m.path, safe="/%:@!$&'()*+,;=-._~"),
                           quote(m.query, safe="=&%:@!$'()*+,;/?-._~"), ""))

    def get(self, url, limite=MAX_OCTETS_PAGE, entetes=None, compter=True, verifier=True):
        if compter and self.nombre >= self.maximum:
            raise RuntimeError("limite globale de pages atteinte")
        if verifier and compter and not self.autorise(url):
            raise PermissionError("refusé par robots.txt")
        hote = urlparse(url).netloc.lower()
        attente = self.delai - (time.time() - self.dernier.get(hote, 0))
        if attente > 0:
            time.sleep(attente)
        h = {"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}
        h.update(entetes or {})
        try:
            with urlopen(Request(self.url_valide(url), headers=h), timeout=25) as rep:
                contenu = rep.read(limite)
                type_contenu = rep.headers.get("Content-Type", "").lower()
                finale = rep.geturl()
        finally:
            self.dernier[hote] = time.time()
            if compter:
                self.nombre += 1
        return contenu, type_contenu, finale


def decoder(contenu, type_contenu=""):
    m = re.search(r"charset=([\w-]+)", type_contenu or "")
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", contenu[:3000], re.I)
        enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    else:
        enc = m.group(1)
    try:
        return contenu.decode(enc, errors="replace")
    except LookupError:
        return contenu.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Lecture du HTML
# ---------------------------------------------------------------------------

def html_vers_texte(html):
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|form|aside)\b[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(?:div|article|li|p|h[1-6]|tr|td|th|section|dd|dt|a)>", "\n", html)
    texte = unescape(re.sub(r"<[^>]+>", " ", html))
    lignes = [re.sub(r"[ \t ]+", " ", l).strip() for l in texte.splitlines()]
    return "\n".join(l for l in lignes if l)


def contenu_principal(html):
    for balise in ("main", "article"):
        m = re.search(rf"(?is)<{balise}\b[^>]*>(.*)</{balise}>", html)
        if m and len(m.group(1)) > 500:
            return m.group(1)
    m = re.search(r"(?is)<body\b[^>]*>(.*)</body>", html)
    return m.group(1) if m else html


def titre_page(html):
    for motif in (r"(?is)<meta[^>]+property=[\"']og:title[\"'][^>]+content=([\"'])(.*?)\1",
                  r"(?is)<h1\b[^>]*()>(.*?)</h1>",
                  r"(?is)<title\b[^>]*()>(.*?)</title>"):
        m = re.search(motif, html)
        if m:
            t = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
            t = re.sub(r"\s*[|\-–—]\s*(?:vetete\.com|miles republic|helloasso|klikego|njuko|gravel passion).*$", "", t, flags=re.I)
            if 4 <= len(t) <= 200:
                return t
    return ""


def extraire_liens(html, base):
    liens = []
    for m in re.finditer(r"""(?is)<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", html):
        href = unescape(m.group(1).strip())
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolu = urldefrag(urljoin(base, href))[0]
        if urlparse(absolu).scheme in ("http", "https"):
            texte = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
            liens.append((absolu, texte[:200]))
    return liens


def objets_jsonld(html):
    """Renvoie les objets schema.org de type Event trouvés dans la page."""
    resultats = []

    def parcourir(obj):
        if isinstance(obj, list):
            for o in obj:
                parcourir(o)
        elif isinstance(obj, dict):
            types = obj.get("@type", "")
            types = types if isinstance(types, list) else [types]
            if any(isinstance(t, str) and t.endswith("Event") for t in types):
                resultats.append(obj)
            for cle in ("@graph", "itemListElement", "item", "subEvent", "event", "events"):
                if cle in obj:
                    parcourir(obj[cle])

    for m in re.finditer(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html):
        try:
            parcourir(json.loads(m.group(1).strip()))
        except (ValueError, RecursionError):
            continue
    return resultats


# ---------------------------------------------------------------------------
# Communes de la zone
# ---------------------------------------------------------------------------

class Communes:
    def __init__(self, liste):
        self.liste = liste
        self.par_nom = {}
        self.par_cp = {}
        for c in liste:
            self.par_nom.setdefault(cle_nom(c["nom"]), []).append(c)
            for cp in c.get("cp", []):
                self.par_cp.setdefault(cp, []).append(c)
        self.longueur_max = max((len(k.split()) for k in self.par_nom), default=1)

    @classmethod
    def charger(cls, config, telechargeur):
        cache = charger_json(CACHE_COMMUNES, None)
        frais = (cache and cache.get("rayon_km") == config["rayon_km"]
                 and cache.get("date", "") >= (AUJOURDHUI - timedelta(days=120)).isoformat())
        if frais:
            return cls(cache["communes"])
        centre = config["centre"]
        communes = []
        try:
            for dep in config["departements_communes"]:
                url = ("https://geo.api.gouv.fr/departements/" + dep + "/communes?"
                       + urlencode({"fields": "nom,codesPostaux,centre,codeDepartement",
                                    "format": "json", "geometry": "centre"}))
                contenu, _, _ = telechargeur.get(url, 20_000_000, compter=False)
                for c in json.loads(contenu):
                    coord = (c.get("centre") or {}).get("coordinates") or []
                    if len(coord) != 2:
                        continue
                    d = distance_km(centre["lat"], centre["lon"], coord[1], coord[0])
                    if d <= config["rayon_km"]:
                        communes.append({"nom": c["nom"], "dep": c["codeDepartement"],
                                         "cp": c.get("codesPostaux", []),
                                         "lat": coord[1], "lon": coord[0], "km": d})
        except Exception as erreur:
            if cache:
                journal(f"Base des communes non actualisée ({erreur}) : ancienne version utilisée")
                return cls(cache["communes"])
            raise SystemExit(f"Impossible de charger la base des communes : {erreur}")
        ecrire_json(CACHE_COMMUNES, {"date": AUJOURDHUI.isoformat(), "rayon_km": config["rayon_km"],
                                     "communes": communes})
        journal(f"Base des communes : {len(communes)} communes à moins de {config['rayon_km']} km")
        return cls(communes)

    # -- recherche dans un texte ------------------------------------------

    def _par_suffixe(self, phrase, dep=None):
        """« Rando de Saint-Lys » → Saint-Lys (essaie les 1 à 6 derniers mots)."""
        mots = cle_nom(phrase).split()
        for n in range(min(6, len(mots)), 0, -1):
            k = " ".join(mots[-n:])
            trouves = [c for c in self.par_nom.get(k, []) if not dep or c["dep"] == dep]
            if len(trouves) == 1:
                return trouves[0]
        return None

    def trouver(self, texte, souple=False):
        """Renvoie (commune, etat). etat : 'sur', 'ambigu' ou None.

        sur : code postal ou (département) explicite, ou nom précédé d'un
        indice de lieu (« à », « départ de », « salle des fêtes de »…).
        souple : accepte aussi un nom seul, s'il est unique dans la zone.
        """
        if not texte:
            return None, None
        # 1. « 31470 Saint-Lys »
        for m in re.finditer(r"\b(\d{5})\b[\s,]*([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\- ]{1,60})", texte):
            suite = cle_nom(m.group(2))
            for c in self.par_cp.get(m.group(1), []):
                if (suite + " ").startswith(cle_nom(c["nom"]) + " "):
                    return c, "sur"
        # 2. « Saint-Lys (31) », « Saint-Lys, 31 », « Saint-Lys - Haute-Garonne (31) »
        for m in re.finditer(r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\- ]{1,60}?)\s*(?:\(|,|–|-)\s*(?:[A-Za-zÀ-ÿ\- ]{3,25}\s*\()?(\d{2}|2[ABab])\s*\)?(?!\d)", texte):
            c = self._par_suffixe(m.group(1), m.group(2).upper())
            if c:
                return c, "sur"
        # 3. Nom précédé d'un indice de lieu, ou nom seul (mode souple)
        mots_originaux = re.findall(r"[A-Za-zÀ-ÿ0-9]+", texte)
        mots = [cle_nom(w) for w in mots_originaux]
        indices = {"a", "au", "depart", "lieu", "commune", "fetes", "stade", "village",
                   "rdv", "rendez", "vous", "organise", "deroulera", "de", "du", "d"}
        ambigu = None
        i = 0
        while i < len(mots):
            trouve = False
            for n in range(min(self.longueur_max, len(mots) - i), 0, -1):
                k = " ".join(mots[i:i + n])
                if k not in self.par_nom:
                    continue
                if len(k) < 4 or k in MOTS_AMBIGUS:
                    break
                # Un nom propre commence par une majuscule dans le texte d'origine.
                if not mots_originaux[i][:1].isupper():
                    break
                precede = i > 0 and mots[i - 1] in indices
                if not (precede or souple):
                    break
                candidats = self.par_nom[k]
                if len(candidats) == 1:
                    return candidats[0], "sur" if precede else "souple"
                ambigu = ambigu or candidats[0]
                i += n
                trouve = True
                break
            if not trouve:
                i += 1
        if ambigu:
            return ambigu, "ambigu"
        return None, None


def libelle_lieu(c):
    return f"{c['nom']} ({c['dep']})"


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MOIS_RE = "|".join(sorted(MOIS, key=len, reverse=True))
_JOURS = r"(?:lun(?:di)?|mar(?:di)?|mer(?:credi)?|jeu(?:di)?|ven(?:dredi)?|sam(?:edi)?|dim(?:anche)?)\.?\s+"
RE_DATE_TEXTE = re.compile(
    r"\b(?:" + _JOURS + r")?(\d{1,2})(?:er|e)?"
    r"(?:\s*(?:et|au|-|–|/|&)\s*(?:" + _JOURS + r")?(\d{1,2})(?:er|e)?)?"
    r"\s+(" + _MOIS_RE + r")\.?(?:\s+(20\d{2}))?\b")
RE_DATE_NUM = re.compile(r"(?<![\d/.])(\d{1,2})[/.](\d{1,2})[/.](20\d{2}|\d{2})(?![\d/.])")
RE_DATE_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})")
RE_CONTEXTE_FAUX = re.compile(
    r"(?:publi|mis a jour|mise a jour|modifi|cree le|poste|ecrit le|actualis|"
    r"jusqu|avant le|date limite|clotur|naissance|copyright|©|derniere|inscriptions? (?:ouvertes|closes|avant|jusqu))[^.\n]{0,30}$")


def date_plausible(d):
    return AUJOURDHUI - timedelta(days=30) <= d <= AUJOURDHUI + timedelta(days=600)


def _annee_probable(mois, jour):
    for annee in (AUJOURDHUI.year, AUJOURDHUI.year + 1):
        try:
            d = date(annee, mois, jour)
        except ValueError:
            return None
        if d >= AUJOURDHUI - timedelta(days=30):
            return d
    return None


def trouver_dates(texte, annee_implicite=True):
    """Liste de (position, debut, fin) des dates plausibles, dans l'ordre du texte."""
    t = normaliser(texte)
    trouvees = []
    for m in RE_DATE_TEXTE.finditer(t):
        if RE_CONTEXTE_FAUX.search(t[max(0, m.start() - 45):m.start()]):
            continue
        j1, j2, mois, annee = m.groups()
        try:
            if annee:
                debut = date(int(annee), MOIS[mois], int(j1))
            elif annee_implicite:
                debut = _annee_probable(MOIS[mois], int(j1))
                if not debut:
                    continue
            else:
                continue
            fin = None
            if j2 and int(j2) > int(j1):
                fin = date(debut.year, debut.month, int(j2))
        except ValueError:
            continue
        trouvees.append((m.start(), debut, fin))
    for m in RE_DATE_NUM.finditer(t):
        if RE_CONTEXTE_FAUX.search(t[max(0, m.start() - 45):m.start()]):
            continue
        j, mo, a = (int(x) for x in m.groups())
        a = a + 2000 if a < 100 else a
        try:
            trouvees.append((m.start(), date(a, mo, j), None))
        except ValueError:
            continue
    for m in RE_DATE_ISO.finditer(t):
        try:
            trouvees.append((m.start(), date(*(int(x) for x in m.groups())), None))
        except ValueError:
            continue
    return sorted((p, d, f) for p, d, f in trouvees if date_plausible(d))


def date_iso(valeur):
    m = RE_DATE_ISO.search(str(valeur or ""))
    if not m:
        return None
    try:
        return date(*(int(x) for x in m.groups()))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Détection des manifestations
# ---------------------------------------------------------------------------

class Collecte:
    def __init__(self, config, communes):
        self.config = config
        self.communes = communes
        self.exclure = [re.compile(r"\b" + re.escape(normaliser(x)) + r"\b")
                        for x in config.get("exclure", [])]
        self.candidats = {}      # (date, commune) -> fiche
        self.a_examiner = {}     # (date, url) -> piste
        self.hors_zone = 0

    def exclu(self, texte):
        t = normaliser(texte)
        return any(x.search(t) for x in self.exclure)

    def ajouter(self, *, titre, debut, fin=None, commune=None, etat=None, url, source,
                trouve_par, extrait="", distance=None, lieu_texte=None):
        titre = re.sub(r"\s+", " ", titre or "").strip(" -–—:|·,")[:140]
        if not titre or self.exclu(titre + " " + url):
            return
        if not date_plausible(debut) or debut < AUJOURDHUI - timedelta(days=7):
            return
        extrait = re.sub(r"\s+", " ", extrait or "").strip()[:400]
        if commune and etat in ("sur", "souple") or distance is not None:
            if commune:
                distance = commune["km"]
                lieu = libelle_lieu(commune)
            else:
                lieu = lieu_texte or "Lieu à préciser"
            if distance > self.config["rayon_km"]:
                self.hors_zone += 1
                return
            cle = (debut.isoformat(), cle_nom(lieu))
            fiche = {
                "date": debut.isoformat(), "title": titre, "location": lieu,
                "details": "Repéré automatiquement — parcours gravel à vérifier avant publication.",
                "source": url, "source_nom": source, "trouve_par": trouve_par,
                "controle_zone": "dans_zone", "distance_toulouse_km": distance,
                "fiabilite_lieu": "certaine" if etat != "souple" else "probable",
                "extrait": extrait,
            }
            if fin and fin > debut:
                fiche["end_date"] = fin.isoformat()
            ancien = self.candidats.get(cle)
            # Une fiche d'événement est plus fiable qu'une ligne de calendrier.
            if not ancien or (ancien["trouve_par"] == "ligne" and trouve_par != "ligne"):
                self.candidats[cle] = fiche
            return
        cle = (debut.isoformat(), url)
        if cle not in self.a_examiner:
            self.a_examiner[cle] = {
                "date": debut.isoformat(), "title": titre, "source": url,
                "source_nom": source, "trouve_par": trouve_par,
                "lieu_suppose": libelle_lieu(commune) if commune else None,
                "raison": "lieu ambigu" if etat == "ambigu" else "lieu non reconnu",
                "extrait": extrait,
            }

    # -- lecture ligne à ligne (calendriers, listes, PDF, extraits web) ---

    def analyser_lignes(self, texte, url, source, trouve_par="ligne"):
        lignes = [l.strip() for l in (texte or "").splitlines() if l.strip()]
        n = 0
        for i, ligne in enumerate(lignes):
            if not MOTIF_GRAVEL.search(normaliser(ligne)):
                continue
            # Fenêtre limitée à la même entrée : on s'arrête aux lignes qui
            # portent une autre date (entrée voisine d'un calendrier).
            fenetre = [ligne]
            for j in (i - 1, i - 2):
                if j < 0 or trouver_dates(lignes[j]):
                    break
                fenetre.insert(0, lignes[j])
            for j in (i + 1, i + 2):
                if j >= len(lignes) or trouver_dates(lignes[j]):
                    break
                fenetre.append(lignes[j])
            bloc = "\n".join(fenetre)
            dates = trouver_dates(ligne) or trouver_dates(bloc)
            if not dates:
                continue
            _, debut, fin = dates[0]
            commune, etat = self.communes.trouver(ligne, souple=len(ligne) < 160)
            if not commune or etat == "ambigu":
                c2, e2 = self.communes.trouver(bloc, souple=False)
                if c2:
                    commune, etat = c2, e2
            titre = self.titre_de_ligne(ligne, fenetre)
            self.ajouter(titre=titre, debut=debut, fin=fin, commune=commune, etat=etat,
                         url=url, source=source, trouve_par=trouve_par, extrait=bloc)
            n += 1
        return n

    @staticmethod
    def titre_de_ligne(ligne, fenetre):
        t = re.sub(r"(?i)\b(?:lun|mar|mer|jeu|ven|sam|dim)[a-z]*\.?\s+\d{1,2}(?:er)?\s+\w+\.?(?:\s+20\d\d)?", " ", ligne)
        t = re.sub(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b", " ", t)
        t = re.sub(r"\s+", " ", t).strip(" -–—:|·,")
        avant = fenetre[:fenetre.index(ligne)]
        if avant and MOTIF_GRAVEL.search(normaliser(avant[-1])) and 6 <= len(avant[-1]) <= 120:
            return avant[-1]
        if len(t) < 6:
            autres = [l for l in fenetre if l != ligne and 6 <= len(l) <= 120]
            t = autres[0] if autres else ligne
        return t[:120]

    # -- fiche d'événement -----------------------------------------------

    def analyser_page(self, html, url, source, trouve_par):
        """Renvoie True si la page ressemble à une liste d'événements (à explorer)."""
        # a) Données structurées schema.org
        for ev in objets_jsonld(html):
            nom = unescape(str(ev.get("name") or ""))
            descr = unescape(str(ev.get("description") or ""))
            if not MOTIF_GRAVEL.search(normaliser(nom + " " + descr + " " + url)):
                continue
            debut = date_iso(ev.get("startDate"))
            if not debut:
                continue
            fin = date_iso(ev.get("endDate"))
            lieu = ev.get("location") or {}
            lieu = lieu[0] if isinstance(lieu, list) and lieu else lieu
            commune, etat, distance, lieu_texte = None, None, None, None
            if isinstance(lieu, dict):
                adr = lieu.get("address") or {}
                if isinstance(adr, dict):
                    lieu_texte = " ".join(str(adr.get(k) or "") for k in ("postalCode", "addressLocality")).strip()
                elif isinstance(adr, str):
                    lieu_texte = adr
                geo = lieu.get("geo") or {}
                commune, etat = self.communes.trouver(lieu_texte or "", souple=True)
                if not commune and isinstance(geo, dict):
                    try:
                        c = self.config["centre"]
                        distance = distance_km(c["lat"], c["lon"], float(geo["latitude"]), float(geo["longitude"]))
                        etat = "sur"
                        lieu_texte = (adr.get("addressLocality") if isinstance(adr, dict) else None) or lieu.get("name")
                    except (KeyError, TypeError, ValueError):
                        pass
            self.ajouter(titre=nom, debut=debut, fin=fin, commune=commune, etat=etat,
                         distance=distance, lieu_texte=lieu_texte, url=str(ev.get("url") or url),
                         source=source, trouve_par="donnees_structurees", extrait=descr)

        principal = contenu_principal(html)
        texte = html_vers_texte(principal)
        t_norm = normaliser(texte)
        titre = titre_page(html)
        nb_lignes_gravel = sum(1 for l in texte.splitlines() if MOTIF_GRAVEL.search(normaliser(l)))
        dates_page = trouver_dates(texte[:20000])
        liste = len({d for _, d, _ in dates_page}) >= 5

        if liste:
            # Page calendrier : chaque ligne gravel datée est une piste.
            self.analyser_lignes(texte, url, source)

        # b) Fiche simple : gravel dans le titre ou au début du contenu
        titre_gravel = bool(MOTIF_GRAVEL.search(normaliser(titre)))
        if not (titre_gravel or (not liste and MOTIF_GRAVEL.search(t_norm[:5000]))):
            return liste or nb_lignes_gravel > 0
        debut = fin = None
        d = trouver_dates(titre)
        if d:
            _, debut, fin = d[0]
        elif liste:
            return True  # page à plusieurs dates : seules les lignes comptent
        if not debut:
            m = re.search(r"(?is)<time\b[^>]*datetime=[\"']([^\"']+)", principal)
            debut = date_iso(m.group(1)) if m else None
            debut = debut if debut and date_plausible(debut) else None
        if not debut and dates_page:
            _, debut, fin = dates_page[0]
        if not debut:
            return False
        commune, etat = self.communes.trouver(titre, souple=True)
        if not commune or etat == "ambigu":
            c2, e2 = self.communes.trouver(texte[:6000], souple=False)
            if c2 and (e2 == "sur" or not commune):
                commune, etat = c2, e2
        self.ajouter(titre=titre or source, debut=debut, fin=fin, commune=commune, etat=etat,
                     url=url, source=source, trouve_par=trouve_par, extrait=texte[:400])
        return liste

    def analyser_ics(self, texte, url, source):
        for bloc in re.findall(r"(?s)BEGIN:VEVENT(.*?)END:VEVENT", texte.replace("\r\n ", "")):
            champs = dict(re.findall(r"(?m)^([A-Z-]+)(?:;[^:]*)?:(.*)$", bloc))
            resume = champs.get("SUMMARY", "")
            if not MOTIF_GRAVEL.search(normaliser(resume + " " + champs.get("DESCRIPTION", ""))):
                continue
            m = re.match(r"(\d{4})(\d{2})(\d{2})", champs.get("DTSTART", ""))
            if not m:
                continue
            debut = date(*(int(x) for x in m.groups()))
            commune, etat = self.communes.trouver(champs.get("LOCATION", "").replace("\\,", ","), souple=True)
            self.ajouter(titre=resume, debut=debut, commune=commune, etat=etat, url=champs.get("URL", url),
                         source=source, trouve_par="ical", extrait=champs.get("DESCRIPTION", ""))


# ---------------------------------------------------------------------------
# Exploration des sites
# ---------------------------------------------------------------------------

EXCLURE_LIENS = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|zip|gpx|kml|mp4|mp3|docx?|xlsx?)(?:$|\?)|"
    r"/(?:wp-admin|wp-login|login|connexion|mon-compte|panier|cart|account|feed|tag|author|"
    r"resultats?|classements?|photos?|galerie|boutique|contact|mentions-legales|cgv)(?:/|$)|"
    r"[?&](?:replytocom|share|print)=", re.I)
RESEAUX = ("facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
           "strava.com", "linkedin.com", "tiktok.com", "whatsapp.com")
MOTS_LIENS = ("evenement", "event", "evt", "agenda", "calendrier", "manifestation", "rando",
              "course", "epreuve", "sortie", "gravel", "raid", "inscription", "programme")


def score_lien(url, texte):
    u = normaliser(unquote(url) + " " + texte)
    s = 0
    if MOTIF_GRAVEL.search(u):
        s += 5
    if re.search(r"20(?:%d|%d)" % (AUJOURDHUI.year % 100, (AUJOURDHUI.year + 1) % 100), u):
        s += 2
    s += sum(1 for m in MOTS_LIENS if m in u)
    return s


def pdf_interessant(url, texte=""):
    u = normaliser(unquote(url) + " " + texte)
    annees = [int(a) for a in re.findall(r"(?<!\d)(20[2-3]\d)(?!\d)", u)]
    if annees and max(annees) < AUJOURDHUI.year:
        return False
    if re.search(r"classement|resultat|podium|reglement|assemblee|compte rendu|bilan|statut", u):
        return False
    return bool(re.search(r"gravel|calendrier|randonn|programme|agenda|manifestation|interclub", u))


def lire_pdf(contenu):
    from pypdf import PdfReader
    lecteur = PdfReader(BytesIO(contenu))
    return "\n".join(page.extract_text() or "" for page in lecteur.pages[:25])


def explorer(nom, depart, collecte, tel, limites, suivre=True, max_pages=None):
    hote = urlparse(depart).netloc.lower().removeprefix("www.")
    max_pages = max_pages or limites["pages_par_site"]
    a_voir = [(100, depart, 0)]
    vus, pdf_vus = set(), set()
    pages = 0
    while a_voir and pages < max_pages:
        a_voir.sort(key=lambda x: -x[0])
        _, url, profondeur = a_voir.pop(0)
        if url in vus:
            continue
        vus.add(url)
        try:
            contenu, type_contenu, finale = tel.get(url)
        except HTTPError as e:
            journal(f"  {nom} : {url} → HTTP {e.code}")
            tel.erreurs += 1
            if e.code in (403, 429) and pages == 0:
                return pages
            continue
        except Exception as e:
            journal(f"  {nom} : {url} → {e}")
            tel.erreurs += 1
            if "limite globale" in str(e):
                return pages
            continue
        pages += 1
        if "pdf" in type_contenu or contenu[:4] == b"%PDF":
            try:
                collecte.analyser_lignes(lire_pdf(contenu), url, nom, "pdf")
            except Exception as e:
                journal(f"  PDF illisible {url} : {e}")
            continue
        if "calendar" in type_contenu or url.lower().endswith(".ics"):
            collecte.analyser_ics(decoder(contenu, type_contenu), url, nom)
            continue
        if type_contenu and not any(t in type_contenu for t in ("html", "text/", "xml")):
            continue
        html = decoder(contenu, type_contenu)
        est_liste = collecte.analyser_page(html, finale, nom, "fiche")
        if (not suivre or profondeur >= 2 or (profondeur >= 1 and not est_liste)
                or (suivre == "si_liste" and not est_liste)):
            continue
        for lien, texte in extraire_liens(html, finale):
            h = urlparse(lien).netloc.lower().removeprefix("www.")
            if h != hote or lien in vus or EXCLURE_LIENS.search(lien):
                continue
            if lien.lower().split("?")[0].endswith(".pdf"):
                if lien not in pdf_vus and len(pdf_vus) < limites["pdf_par_site"] and pdf_interessant(lien, texte):
                    pdf_vus.add(lien)
                    a_voir.append((50, lien, profondeur + 1))
                continue
            s = score_lien(lien, texte)
            if s >= (1 if profondeur == 0 else 3):
                a_voir.append((s, lien, profondeur + 1))
    return pages


# ---------------------------------------------------------------------------
# Recherche web (Brave Search)
# ---------------------------------------------------------------------------

def requetes_web(config):
    rw = config.get("recherche_web", {})
    annee = AUJOURDHUI.year if AUJOURDHUI.month < 9 else AUJOURDHUI.year + 1
    requetes = []
    for modele in rw.get("requetes_par_departement", []):
        for dep in rw.get("departements", []):
            requetes.append(modele.format(departement=dep, annee=annee))
    for modele in rw.get("requetes_generales", []):
        requetes.append(modele.format(annee=annee))
    return list(dict.fromkeys(requetes))


def recherche_brave(config, collecte, tel, limites):
    cle = os.environ.get("BRAVE_API_KEY", "").strip()
    if not cle:
        journal("Recherche web désactivée : secret BRAVE_API_KEY absent.")
        return 0, 0
    nb = config.get("recherche_web", {}).get("resultats_par_requete", 20)
    urls = {}
    for requete in requetes_web(config):
        params = urlencode({"q": requete, "country": "fr", "search_lang": "fr", "count": nb})
        try:
            contenu, _, _ = tel.get("https://api.search.brave.com/res/v1/web/search?" + params, compter=False,
                                    entetes={"Accept": "application/json", "X-Subscription-Token": cle})
            resultats = json.loads(contenu).get("web", {}).get("results", [])
        except HTTPError as e:
            journal(f"Brave : « {requete} » → HTTP {e.code}")
            if e.code in (401, 403):
                journal("Brave : clé refusée, recherche web interrompue.")
                break
            if e.code == 429:
                time.sleep(2)
            continue
        except Exception as e:
            journal(f"Brave : « {requete} » → {e}")
            continue
        time.sleep(1.1)  # offre gratuite : 1 requête par seconde
        for r in resultats:
            url = urldefrag(r.get("url", ""))[0]
            if not url:
                continue
            extrait = unescape(re.sub(r"<[^>]+>", "", f"{r.get('title', '')}\n{r.get('description', '')}"))
            if url not in urls:
                urls[url] = extrait
    journal(f"Brave : {len(urls)} pages distinctes trouvées")
    explorees = 0
    for url, extrait in urls.items():
        hote = urlparse(url).netloc.lower()
        # Le résumé du moteur suffit parfois (Facebook, Instagram ne sont pas lisibles).
        collecte.analyser_lignes(extrait, url, "Recherche web", "extrait_web")
        if any(hote.endswith(r) for r in RESEAUX):
            continue
        if not MOTIF_GRAVEL.search(normaliser(extrait + " " + url)):
            continue
        try:
            explorer("Recherche web", url, collecte, tel, limites, suivre="si_liste",
                     max_pages=limites["pages_par_resultat_web"])
            explorees += 1
        except Exception as e:
            journal(f"  Recherche web : {url} → {e}")
    return len(urls), explorees


# ---------------------------------------------------------------------------
# Fusion avec l'inventaire (les décisions humaines restent prioritaires)
# ---------------------------------------------------------------------------

def meme_manifestation(a, b):
    if a.get("date") != b.get("date"):
        return False
    la, lb = cle_nom(re.sub(r"\(.*?\)", "", a.get("location", ""))), cle_nom(re.sub(r"\(.*?\)", "", b.get("location", "")))
    if la and lb and (la == lb or la in lb or lb in la):
        return True
    ta, tb = cle_titre(a.get("title", "")), cle_titre(b.get("title", ""))
    return bool(ta and tb and len(min(ta, tb, key=len)) >= 5 and (ta in tb or tb in ta))


def fusionner(candidats):
    inventaire = charger_json(INVENTAIRE, {"events": []})
    if not isinstance(inventaire.get("events"), list):
        raise SystemExit("Format de l'inventaire incorrect : arrêt sans écrasement")
    fiches = inventaire["events"]
    calendrier = charger_json(CALENDRIER, {"events": []})
    existantes = fiches + calendrier.get("events", [])
    nouvelles = []
    for c in sorted(candidats, key=lambda f: (f["date"], f["title"])):
        if any(meme_manifestation(c, f) for f in existantes):
            continue
        fiche = {k: v for k, v in c.items() if k != "extrait"}
        fiche["statut"] = "a_verifier"
        fiche["date_detection"] = AUJOURDHUI.isoformat()
        fiches.append(fiche)
        existantes.append(fiche)
        nouvelles.append(fiche)
    fiches.sort(key=lambda f: (f.get("date", ""), f.get("title", "")))
    inventaire["updated"] = AUJOURDHUI.isoformat()
    ecrire_json(INVENTAIRE, inventaire)
    return nouvelles


def main():
    config = charger_json(CONFIG, None)
    if not config:
        raise SystemExit("sources.json introuvable")
    limites = config["limites"]
    tel = Telechargeur(limites["delai_par_site_s"], limites["pages_total"])
    communes = Communes.charger(config, tel)
    collecte = Collecte(config, communes)

    stats = {"sites": {}}
    for site in config.get("sites", []):
        avant = len(collecte.candidats) + len(collecte.a_examiner)
        try:
            pages = explorer(site["nom"], site["url"], collecte, tel, limites,
                             suivre=site.get("suivre_liens", True))
        except Exception as e:
            journal(f"Erreur sur {site['nom']} : {e}")
            pages = 0
        trouves = len(collecte.candidats) + len(collecte.a_examiner) - avant
        stats["sites"][site["nom"]] = {"pages_lues": pages, "pistes": trouves}
        journal(f"{site['nom']} : {pages} pages lues, {trouves} pistes")

    stats["recherche_web_pages"], stats["recherche_web_explorees"] = recherche_brave(config, collecte, tel, limites)

    candidats = list(collecte.candidats.values())
    nouvelles = fusionner(candidats)

    # Les pistes sans lieu reconnu sont conservées d'une exécution à l'autre.
    anciens = charger_json(RESULTATS, {})
    pistes = {(p["date"], p["source"]): p for p in anciens.get("a_examiner", [])
              if p.get("date", "") >= AUJOURDHUI.isoformat()}
    for cle, p in collecte.a_examiner.items():
        if p["date"] >= AUJOURDHUI.isoformat() and not any(
                c["date"] == p["date"] and c["source"] == p["source"] for c in candidats):
            pistes.setdefault(cle, dict(p, date_detection=AUJOURDHUI.isoformat()))

    stats.update({"pages_lues": tel.nombre, "erreurs": tel.erreurs, "candidats": len(candidats),
                  "nouvelles_fiches": len(nouvelles), "hors_zone": collecte.hors_zone,
                  "a_examiner": len(pistes)})
    ecrire_json(RESULTATS, {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "statistiques": stats,
        "candidats": sorted(candidats, key=lambda f: f["date"]),
        "a_examiner": sorted(pistes.values(), key=lambda p: p["date"])[:300],
    })
    journal(f"\nBilan : {tel.nombre} pages lues, {len(candidats)} manifestations reconnues, "
            f"{len(nouvelles)} nouvelles fiches à vérifier, {len(pistes)} pistes sans lieu reconnu, "
            f"{collecte.hors_zone} hors rayon.")
    for f in nouvelles:
        journal(f"  + {f['date']} — {f['title']} — {f['location']} — {f['source']}")


if __name__ == "__main__":
    sys.exit(main())
