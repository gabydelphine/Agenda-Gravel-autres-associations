#!/usr/bin/env python3
"""Veille du calendrier « Ultra gravel » (ultra.json).

Épreuves gravel ou bikepacking dont un parcours fait 150 km ou plus, avec un départ
à moins de 350 km de Toulouse. Réglages dans ultra-sources.json.

Chaque jour :
  1. les pages de calendrier listées (Finishers, gravelevents…) et une recherche
     web (Brave, clé BRAVE_API_KEY) font découvrir des épreuves absentes du
     calendrier ;
  2. chaque fiche est lue (données schema.org en priorité, sinon le texte) :
     date, distances, lieu (géocodé avec OpenStreetMap), gravel ou bikepacking ;
  3. les épreuves dont la date n'est encore qu'approximative sont relues sur
     leur source, pour repérer une date précise.

Rien n'est publié automatiquement : tout part dans ultra-veille.json, puis dans
l'e-mail du dimanche, avec des boutons « Ajouter », « Ignorer » ou « Mettre la
date à jour ». Ces boutons créent une demande GitHub, qui lance :
    python veille_ultra.py --decision ajouter|ajouter-asso|ajouter-prive|refuser|date --id <identifiant>
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from html import unescape
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urldefrag, urlencode, urlparse

from veille_gravel import (AUJOURDHUI, RESEAUX, Telechargeur, charger_json, cle_titre,
                           contenu_principal, date_iso, decoder, distance_km, ecrire_json,
                           extraire_liens, html_vers_texte, journal, normaliser,
                           objets_jsonld, titre_page, trouver_dates)

CONFIG = Path("ultra-sources.json")
CALENDRIER = Path("ultra.json")
VEILLE = Path("ultra-veille.json")
CACHE_GEO = Path("cache/geo-ultra.json")

MOTS_BIKEPACKING = re.compile(r"bikepacking|autonomie|self[- ]?supported|unsupported|sans assistance|bivouac")
MOTS_GRAVEL = re.compile(r"\bgravels?\b|\bgravier\b|\bpistes?\b")
MOTS_ROUTE_SEULE = re.compile(r"\b(?:cyclosportive|gran ?fondo|brm|brevet|audax|contre-la-montre)\b")
RE_KM = re.compile(r"(?<![\d.,])(\d{1,2}[  .]?\d{3}|\d{3,4})(?:[.,]\d)?\s?(?:km|kilometres?|kilomètres?)\b", re.I)
RE_MILES = re.compile(r"(?<![\d.,])(\d{3,4})\s?(?:miles?|millas|mi)\b", re.I)


def identifiant(*morceaux):
    return hashlib.sha256("|".join(normaliser(m) for m in morceaux).encode()).hexdigest()[:16]


def distances(texte):
    vus = set()
    for m in RE_KM.finditer(texte):
        vus.add(int(re.sub(r"\D", "", m.group(1))))
    for m in RE_MILES.finditer(texte):
        vus.add(round(int(m.group(1)) * 1.609))
    return sorted(k for k in vus if 150 <= k <= 5000)[:6]


class Geocodeur:
    """Lieu → coordonnées avec Nominatim (OpenStreetMap), mis en cache."""

    def __init__(self, tel):
        self.tel = tel
        self.cache = charger_json(CACHE_GEO, {})
        self.dernier = 0

    def _requete(self, q):
        attente = 1.1 - (time.time() - self.dernier)
        if attente > 0:
            time.sleep(attente)
        self.dernier = time.time()
        params = urlencode({"q": q, "format": "json", "limit": 5, "addressdetails": 1,
                            "countrycodes": "fr,es,ad", "accept-language": "fr"})
        contenu, _, _ = self.tel.get("https://nominatim.openstreetmap.org/search?" + params,
                                     compter=False, verifier=False)
        return json.loads(contenu)

    def chercher(self, lieu):
        """Le résultat doit contenir la commune citée en premier : sinon OpenStreetMap
        renvoie parfois le centre de la région (« Baza, Catalogne » → Gérone)."""
        cle = normaliser(lieu)
        if not cle:
            return None
        if cle in self.cache:
            return self.cache[cle]
        commune = normaliser(re.split(r"[,(]", lieu)[0])
        resultat = None
        try:
            for q in dict.fromkeys([lieu, re.split(r"[,(]", lieu)[0].strip()]):
                for r in self._requete(q):
                    adr = r.get("address", {})
                    noms = " ".join(normaliser(adr.get(k, "")) for k in
                                    ("city", "town", "village", "municipality", "hamlet", "suburb"))
                    if commune and commune in (noms + " " + normaliser(r.get("name", ""))):
                        pays = (adr.get("country_code") or "").upper()
                        resultat = {"c": [round(float(r["lat"]), 3), round(float(r["lon"]), 3)],
                                    "p": "ES" if pays == "ES" else "FR"}
                        break
                if resultat:
                    break
        except Exception as e:
            journal(f"  géocodage « {lieu} » → {e}")
            return None
        self.cache[cle] = resultat
        return resultat


def lieu_jsonld(ev):
    lieu = ev.get("location") or {}
    if isinstance(lieu, list):
        lieu = lieu[0] if lieu else {}
    if isinstance(lieu, str):
        return lieu, None
    geo = lieu.get("geo") or {}
    coords = None
    try:
        coords = [round(float(geo["latitude"]), 3), round(float(geo["longitude"]), 3)]
    except (KeyError, TypeError, ValueError):
        pass
    adr = lieu.get("address") or {}
    if isinstance(adr, str):
        texte = adr
    else:
        texte = ", ".join(str(adr.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")
                          if adr.get(k) and isinstance(adr.get(k), str))
    return texte or lieu.get("name", ""), coords


def lire_fiche(html, url):
    """Renvoie un dictionnaire {n, d, f, lieu, coords, k, texte} ou None."""
    texte = html_vers_texte(contenu_principal(html))
    for ev in objets_jsonld(html):
        debut = date_iso(ev.get("startDate"))
        if not ev.get("name") or not debut:
            continue
        lieu, coords = lieu_jsonld(ev)
        desc = unescape(re.sub(r"<[^>]+>", " ", str(ev.get("description", ""))))
        return {"n": re.sub(r"\s+", " ", unescape(str(ev["name"]))).strip(), "d": debut,
                "f": date_iso(ev.get("endDate")), "lieu": lieu, "coords": coords,
                "k": distances(f"{ev['name']} {desc} {texte}"), "texte": f"{ev['name']} {desc} {texte}"}
    titre = titre_page(html)
    dates = trouver_dates(texte[:6000])
    if not titre or not dates:
        return None
    _, debut, fin = dates[0]
    return {"n": titre, "d": debut, "f": fin, "lieu": "", "coords": None,
            "k": distances(f"{titre} {texte}"), "texte": f"{titre} {texte}"}


def type_epreuve(texte):
    t = normaliser(texte)
    if MOTS_BIKEPACKING.search(t):
        return "bikepacking"
    if MOTS_GRAVEL.search(t):
        return "gravel"
    return None


class Veille:
    def __init__(self, config):
        self.config = config
        lim = config.get("limites", {})
        self.tel = Telechargeur(lim.get("delai_par_site_s", 1.0), lim.get("pages_total", 250))
        self.geo = Geocodeur(self.tel)
        self.centre = (config["centre"]["lat"], config["centre"]["lon"])
        self.rayon = config.get("rayon_km", 350)
        self.km_min = config.get("distance_min_km", 150)
        self.calendrier = charger_json(CALENDRIER, {"evenements": []})
        self.etat = charger_json(VEILLE, {})
        self.connus_nom = {cle_titre(e["n"]) for e in self.calendrier.get("evenements", [])}
        self.connus_url = {urldefrag(e.get("s", ""))[0] for e in self.calendrier.get("evenements", [])}
        self.refusees = set(self.etat.get("refusees", []))
        self.nouveautes = {n["id"]: n for n in self.etat.get("nouveautes", [])}
        self.pistes = {}
        self.vues = set()

    def deja_connu(self, nom, url):
        cle = cle_titre(nom)
        if url in self.connus_url or not cle:
            return True
        return any(cle == c or (len(cle) > 6 and (cle in c or c in cle)) for c in self.connus_nom)

    def examiner(self, url, origine):
        url = urldefrag(url)[0]
        hote = urlparse(url).netloc.lower()
        if url in self.vues or any(hote == r or hote.endswith("." + r) for r in RESEAUX):
            return
        self.vues.add(url)
        try:
            contenu, type_contenu, finale = self.tel.get(url)
        except Exception as e:
            journal(f"  {url} → {e}")
            return
        if "html" not in type_contenu:
            return
        fiche = lire_fiche(decoder(contenu, type_contenu), finale)
        if not fiche or self.deja_connu(fiche["n"], finale):
            return
        debut = fiche["d"]
        if not (AUJOURDHUI <= debut <= AUJOURDHUI + timedelta(days=600)):
            return
        sorte = type_epreuve(fiche["texte"])
        if not sorte or not fiche["k"] or max(fiche["k"]) < self.km_min:
            return
        if MOTS_ROUTE_SEULE.search(normaliser(fiche["n"])) and sorte != "bikepacking":
            return
        num = identifiant(fiche["n"], debut.year)
        if num in self.refusees or num in self.nouveautes:
            return
        coords, pays = fiche["coords"], None
        if not coords and fiche["lieu"]:
            g = self.geo.chercher(fiche["lieu"])
            if g:
                coords, pays = g["c"], g["p"]
        base = {"id": num, "n": fiche["n"], "d": debut.isoformat(),
                "f": fiche["f"].isoformat() if fiche["f"] and fiche["f"] > debut else None,
                "l": fiche["lieu"], "k": [k for k in fiche["k"] if k >= self.km_min], "t": sorte,
                "s": finale, "trouve_par": origine, "vu_le": AUJOURDHUI.isoformat()}
        if not coords:
            self.pistes[num] = base
            return
        d = distance_km(*self.centre, *coords)
        if d > self.rayon:
            return
        if not pays:
            g = self.geo.chercher(fiche["lieu"]) if fiche["lieu"] else None
            pays = g["p"] if g else "FR"
        base.update({"c": coords, "p": pays, "distance_toulouse_km": round(d)})
        self.nouveautes[num] = base
        journal(f"  + {base['d']} — {base['n']} — {base['l']} — {round(d)} km de Toulouse")

    def listes(self):
        nb = self.config.get("limites", {}).get("fiches_par_liste", 40)
        for liste in self.config.get("listes", []):
            journal(f"Liste : {liste['nom']}")
            try:
                contenu, type_contenu, finale = self.tel.get(liste["url"])
            except Exception as e:
                journal(f"  {liste['url']} → {e}")
                continue
            hote = urlparse(finale).netloc
            motif = re.compile(liste.get("motif_liens", "."))
            liens = [u for u, t in extraire_liens(decoder(contenu, type_contenu), finale)
                     if urlparse(u).netloc == hote and motif.search(urlparse(u).path) and u != finale]
            for url in list(dict.fromkeys(liens))[:nb]:
                self.examiner(url, liste["nom"])

    def recherche_web(self):
        cle = os.environ.get("BRAVE_API_KEY", "").strip()
        rw = self.config.get("recherche_web", {})
        if not cle:
            journal("Recherche web désactivée : secret BRAVE_API_KEY absent.")
            return
        annee = AUJOURDHUI.year if AUJOURDHUI.month < 9 else AUJOURDHUI.year + 1
        for modele in rw.get("requetes", []):
            requete = modele.format(annee=annee)
            params = urlencode({"q": requete, "country": "fr", "count": rw.get("resultats_par_requete", 15)})
            try:
                contenu, _, _ = self.tel.get("https://api.search.brave.com/res/v1/web/search?" + params,
                                             compter=False, verifier=False,
                                             entetes={"Accept": "application/json", "X-Subscription-Token": cle})
                resultats = json.loads(contenu).get("web", {}).get("results", [])
            except HTTPError as e:
                journal(f"Brave : « {requete} » → HTTP {e.code}")
                if e.code in (401, 403):
                    return
                continue
            except Exception as e:
                journal(f"Brave : « {requete} » → {e}")
                continue
            time.sleep(1.1)
            for r in resultats:
                if r.get("url"):
                    self.examiner(r["url"], "Recherche web")

    def dates_a_preciser(self):
        """Relit la source des épreuves dont la date n'est qu'approximative."""
        propositions = {p["id"]: p for p in self.etat.get("mises_a_jour", [])}
        for e in self.calendrier.get("evenements", []):
            if not e.get("a") or not str(e.get("s", "")).startswith("http"):
                continue
            num = identifiant("date", e["n"], e["d"])
            if num in self.refusees or num in propositions:
                continue
            try:
                contenu, type_contenu, finale = self.tel.get(e["s"])
            except Exception as err:
                journal(f"  {e['n']} : {err}")
                continue
            html = decoder(contenu, type_contenu)
            prevue = date.fromisoformat(e["d"])
            trouvees = []
            for ev in objets_jsonld(html):
                d = date_iso(ev.get("startDate"))
                if d:
                    trouvees.append((d, date_iso(ev.get("endDate"))))
            if not trouvees:
                trouvees = [(d, f) for _, d, f in trouver_dates(html_vers_texte(contenu_principal(html))[:6000])]
            for d, f in trouvees:
                if abs((d - prevue).days) <= 45 and d >= AUJOURDHUI:
                    propositions[num] = {"id": num, "n": e["n"], "d_actuelle": e.get("a"), "d": d.isoformat(),
                                         "f": f.isoformat() if f and f > d else None, "s": finale,
                                         "vu_le": AUJOURDHUI.isoformat()}
                    journal(f"  date trouvée pour {e['n']} : {d.isoformat()}")
                    break
        return list(propositions.values())

    def lancer(self):
        self.listes()
        self.recherche_web()
        mises_a_jour = self.dates_a_preciser()
        # Les nouveautés déjà passées ou ajoutées entre-temps disparaissent.
        nouveautes = [n for n in self.nouveautes.values()
                      if n["d"] >= AUJOURDHUI.isoformat() and not self.deja_connu(n["n"], n["s"])]
        ecrire_json(VEILLE, {
            "_aide": "Résultats de veille_ultra.py. Rien n'est publié sans décision : "
                     "voir l'e-mail du dimanche. « refusees » : épreuves et dates à ne plus proposer.",
            "maj": AUJOURDHUI.isoformat(),
            "nouveautes": sorted(nouveautes, key=lambda n: n["d"]),
            "mises_a_jour": mises_a_jour,
            "pistes": sorted(self.pistes.values(), key=lambda n: n["d"])[:40],
            "refusees": sorted(self.refusees),
        })
        ecrire_json(CACHE_GEO, self.geo.cache)
        journal(f"{len(nouveautes)} nouveauté(s), {len(mises_a_jour)} date(s) à préciser, "
                f"{len(self.pistes)} piste(s) sans lieu, {self.tel.nombre} page(s) lues")


def decider(decision, num):
    etat = charger_json(VEILLE, {})
    cal = charger_json(CALENDRIER, {"evenements": []})
    refusees = set(etat.get("refusees", []))
    if decision == "refuser":
        refusees.add(num)
        etat["nouveautes"] = [n for n in etat.get("nouveautes", []) if n["id"] != num]
        etat["mises_a_jour"] = [m for m in etat.get("mises_a_jour", []) if m["id"] != num]
    elif decision in ("ajouter", "ajouter-asso", "ajouter-prive"):
        fiche = next((n for n in etat.get("nouveautes", []) if n["id"] == num), None)
        if not fiche:
            raise SystemExit(f"Nouveauté {num} introuvable.")
        cal["evenements"].append({k: v for k, v in {
            "n": fiche["n"], "d": fiche["d"], "f": fiche.get("f"), "l": fiche["l"], "p": fiche["p"],
            "c": fiche["c"], "k": fiche["k"], "t": fiche["t"],
            "note": "Ajouté par la veille, à compléter si besoin.", "s": fiche["s"],
            "o": {"ajouter-asso": "asso", "ajouter-prive": "prive"}.get(decision)}.items() if v})
        etat["nouveautes"] = [n for n in etat["nouveautes"] if n["id"] != num]
        refusees.add(num)  # déjà traitée : ne plus la proposer
    elif decision == "date":
        prop = next((m for m in etat.get("mises_a_jour", []) if m["id"] == num), None)
        if not prop:
            raise SystemExit(f"Mise à jour {num} introuvable.")
        for e in cal["evenements"]:
            if e["n"] == prop["n"]:
                e["d"] = prop["d"]
                e.pop("a", None)
                if prop.get("f"):
                    e["f"] = prop["f"]
                else:
                    e.pop("f", None)
        etat["mises_a_jour"] = [m for m in etat["mises_a_jour"] if m["id"] != num]
    else:
        raise SystemExit("Décision inconnue.")
    etat["refusees"] = sorted(refusees)
    cal["evenements"].sort(key=lambda e: e["d"])
    cal["maj"] = AUJOURDHUI.isoformat()
    CALENDRIER.write_text(json.dumps(cal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ecrire_json(VEILLE, etat)
    journal(f"Décision « {decision} » enregistrée pour {num}.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--decision", choices=["ajouter", "ajouter-asso", "ajouter-prive", "refuser", "date"])
    p.add_argument("--id")
    a = p.parse_args()
    if a.decision:
        if not a.id or not re.fullmatch(r"[0-9a-f]{16}", a.id):
            raise SystemExit("Identifiant invalide.")
        decider(a.decision, a.id)
        return 0
    Veille(charger_json(CONFIG, {})).lancer()
    return 0


if __name__ == "__main__":
    sys.exit(main())
