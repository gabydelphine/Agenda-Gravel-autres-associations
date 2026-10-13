
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RESULTATS = Path("veille-resultats.json")
HISTORIQUE = Path("veille-notifications.json")

if not RESULTATS.exists():
    print("Aucun fichier de veille.")
    raise SystemExit(0)

donnees = json.loads(RESULTATS.read_text(encoding="utf-8"))
observations = donnees.get("observations", [])

if HISTORIQUE.exists():
    historique = json.loads(
        HISTORIQUE.read_text(encoding="utf-8")
    )
else:
    historique = {"envoyes": []}

deja_envoyes = set(historique.get("envoyes", []))


def identifiant(item):
    return item.get("source", "") + "|" + item.get("extrait", "")


nouveautes = [
    item for item in observations
    if identifiant(item) not in deja_envoyes
]

if not nouveautes:
    print("Aucune nouvelle observation a envoyer.")
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

message = {
    "sender": {
        "name": "Veille Gravel Toulouse",
        "email": os.environ["BREVO_SENDER_EMAIL"]
    },
    "to": [{
        "email": os.environ["NOTIFICATION_EMAIL"]
    }],
    "subject": (
        f"Veille Gravel - {len(nouveautes)} nouveaute(s)"
    ),
    "textContent": "\n".join(lignes)
}

requete = Request(
    "https://api.brevo.com/v3/smtp/email",
    data=json.dumps(message).encode("utf-8"),
    headers={
        "api-key": os.environ["BREVO_API_KEY"],
        "Content-Type": "application/json",
        "Accept": "application/json"
    },
    method="POST"
)

try:
    with urlopen(requete, timeout=30) as reponse:
        print(f"Envoi accepte par Brevo : HTTP {reponse.status}")
except HTTPError as erreur:
    print(f"Erreur Brevo : HTTP {erreur.code}")
    raise SystemExit(1)
except URLError as erreur:
    print(f"Connexion impossible : {erreur.reason}")
    raise SystemExit(1)

deja_envoyes.update(identifiant(item) for item in nouveautes)

HISTORIQUE.write_text(
    json.dumps(
        {"envoyes": sorted(deja_envoyes)},
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

print(f"{len(nouveautes)} observations marquees comme envoyees.")

