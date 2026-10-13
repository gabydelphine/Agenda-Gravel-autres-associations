
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

CALENDRIER = Path("events.json")
OBSERVATIONS = Path("veille-resultats.json")

SOURCE_PDF = (
    "https://www.cyclotourisme-31.com/"
    "uploaded/Circuits_InterClubs/2026/"
    "saubensgravel2026.pdf"
)

def normaliser(texte):
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(
        c for c in texte if not unicodedata.combining(c)
    )
    return re.sub(r"\s+", " ", texte).strip().casefold()

def evenement_existant(evenements, nouveau):
    titre = normaliser(nouveau["title"])
    for evenement in evenements:
        if evenement.get("date") != nouveau["date"]:
            continue
        ancien_titre = normaliser(
            evenement.get("title", "")
        )
        if (
            titre == ancien_titre
            or titre in ancien_titre
            or ancien_titre in titre
        ):
            return True
    return False

def analyser_observation(observation):
    extrait = observation.get("extrait", "")
    texte = normaliser(extrait)
    date_evenement = observation.get("date_evenement")

    # Manifestation confirmée par le PDF de Saubens
    if (
        observation.get("url") == SOURCE_PDF
        and "saubenoise" in texte
        and "gravel" in texte
    ):
        return {
            "date": "2026-10-18",
            "title": "La Ronde Saubenoise",
            "location": "Saubens (31)",
            "details": "Gravel : 47 km · 721 m D+",
            "source": SOURCE_PDF
        }

    # Annonces structurées de Vélo Occitanie
    if (
        observation.get("source") != "Velo Occitanie"
        or not date_evenement
        or "gravel" not in texte
    ):
        return None

    correspondance = re.search(
        r"(?i)détails\s+(.+?)\s+"
        r"(?:lun|mar|mer|jeu|ven|sam|dim)\.?\s+"
        r"\d{1,2}\s+[a-zéûô]+\s+20\d{2}",
        extrait
    )

    if not correspondance:
        return None

    titre = correspondance.group(1).strip()

    # Les lieux ne sont retenus que s'ils sont identifiables.
    lieux = {
        "raid du lys": "Saint-Lys (31)",
        "o gravel occitanie 600": "Garidech (31)"
    }

    lieu = None
    titre_normalise = normaliser(titre)

    for nom, ville in lieux.items():
        if nom in titre_normalise:
            lieu = ville
            break

    if not lieu:
        print(f"Lieu non confirme : {titre}")
        return None

    return {
        "date": date_evenement,
        "title": titre,
        "location": lieu,
        "details": "Parcours Gravel annoncé",
        "source": observation.get("url", "")
    }

if not CALENDRIER.exists() or not OBSERVATIONS.exists():
    raise SystemExit("Fichier calendrier ou veille introuvable")

calendrier = json.loads(
    CALENDRIER.read_text(encoding="utf-8")
)
donnees = json.loads(
    OBSERVATIONS.read_text(encoding="utf-8")
)

evenements = calendrier.get("events", [])
ajouts = 0

for observation in donnees.get("observations", []):
    nouveau = analyser_observation(observation)

    if nouveau is None:
        continue

    try:
        jour = date.fromisoformat(nouveau["date"])
    except (ValueError, TypeError):
        continue

    if jour < date.today():
        continue

    if evenement_existant(evenements, nouveau):
        print(f"Deja present : {nouveau['title']}")
        continue

    evenements.append(nouveau)
    ajouts += 1
    print(f"Ajoute : {nouveau['title']}")

evenements.sort(key=lambda e: e.get("date", ""))

if ajouts:
    calendrier["events"] = evenements
    calendrier["updated"] = date.today().isoformat()

    CALENDRIER.write_text(
        json.dumps(
            calendrier,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

print(f"{ajouts} nouvelle(s) manifestation(s) ajoutee(s)")
print(f"{len(evenements)} manifestations au calendrier")
