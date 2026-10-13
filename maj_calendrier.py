
import json
from datetime import date
from pathlib import Path

CALENDRIER = Path("events.json")
OBSERVATIONS = Path("veille-resultats.json")

SOURCE_PDF = (
    "https://www.cyclotourisme-31.com/"
    "uploaded/Circuits_InterClubs/2026/"
    "saubensgravel2026.pdf"
)

if not CALENDRIER.exists():
    raise SystemExit("Erreur : events.json introuvable")

calendrier = json.loads(
    CALENDRIER.read_text(encoding="utf-8")
)

donnees = json.loads(
    OBSERVATIONS.read_text(encoding="utf-8")
)

evenements = calendrier.get("events", [])

for observation in donnees.get("observations", []):
    if observation.get("url") != SOURCE_PDF:
        continue

    extrait = observation.get("extrait", "").lower()

    if "saubenoise" not in extrait or "gravel" not in extrait:
        continue

    nouvel_evenement = {
        "date": "2026-10-18",
        "title": "La Ronde Saubenoise",
        "location": "Saubens (31)",
        "details": "Gravel : 47 km · 721 m D+",
        "source": SOURCE_PDF
    }

    existe = any(
        e.get("date") == nouvel_evenement["date"]
        and (
            "saubenoise" in e.get("title", "").lower()
            or e.get("source") == SOURCE_PDF
        )
        for e in evenements
    )

    if not existe:
        evenements.append(nouvel_evenement)
        print("Evenement ajoute : La Ronde Saubenoise")
    else:
        print("La Ronde Saubenoise est deja presente")

    break

evenements.sort(key=lambda e: e.get("date", ""))

calendrier["events"] = evenements
calendrier["updated"] = date.today().isoformat()

CALENDRIER.write_text(
    json.dumps(calendrier, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8"
)

print(f"Calendrier : {len(evenements)} evenements")
