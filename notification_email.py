
import json
import os
from datetime import date, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

fichier = Path("veille-resultats.json")

if not fichier.exists():
    print("Aucun fichier de veille.")
    raise SystemExit(0)

donnees = json.loads(fichier.read_text(encoding="utf-8"))
observations = donnees.get("observations", [])

limite = date.today() - timedelta(days=7)
nouveautes = []

for item in observations:
    try:
        detection = date.fromisoformat(item["date_detection"])
        if detection >= limite:
            nouveautes.append(item)
    except (KeyError, ValueError):
        continue

if not nouveautes:
    print("Aucune nouveaute cette semaine.")
    raise SystemExit(0)

lignes = [
    "Bonjour,",
    "",
    "Voici les nouvelles observations de la veille gravel.",
    "Ces informations doivent etre verifiees avant publication.",
    "",
]

for numero, item in enumerate(nouveautes, 1):
    lignes.extend([
        f"{numero}. Source : {item.get('source', '')}",
        f"Lien : {item.get('url', '')}",
        f"Extrait : {item.get('extrait', '')}",
        "",
    ])

lignes.append("Veille Gravel Toulouse")

destinataire = os.environ["NOTIFICATION_EMAIL"]
cle_api = os.environ["BREVO_API_KEY"]
expediteur = os.environ["BREVO_SENDER_EMAIL"]

message = {
    "sender": {
        "name": "Veille Gravel Toulouse",
        "email": expediteur
    },
    "to": [{"email": destinataire}],
    "subject": f"Veille Gravel - {len(nouveautes)} observation(s)",
    "textContent": "\n".join(lignes)
}

requete = Request(
    "https://api.brevo.com/v3/smtp/email",
    data=json.dumps(message).encode("utf-8"),
    headers={
        "api-key": cle_api,
        "Content-Type": "application/json",
        "Accept": "application/json"
    },
    method="POST"
)

try:
    with urlopen(requete, timeout=30) as reponse:
        print(f"Demande d'envoi acceptee : HTTP {reponse.status}")
except HTTPError as erreur:
    print(f"Erreur Brevo : HTTP {erreur.code}")
    raise SystemExit(1)
except URLError as erreur:
    print(f"Connexion Brevo impossible : {erreur.reason}")
    raise SystemExit(1)

