
import json
import os
import smtplib
import ssl
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

FICHIER = Path("veille-resultats.json")

if not FICHIER.exists():
    print("Aucun resultat de veille.")
    raise SystemExit(0)

donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
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

destinataire = os.environ["NOTIFICATION_EMAIL"]
expediteur = os.environ["SMTP_USERNAME"]
mot_de_passe = os.environ["SMTP_PASSWORD"]

lignes = [
    "Bonjour,",
    "",
    "Voici les nouvelles observations de la veille gravel.",
    "Ces informations doivent etre verifiees avant publication.",
    "",
]

for numero, item in enumerate(nouveautes, 1):
    lignes.extend([
        f"{numero}. Source : {item['source']}",
        f"Lien : {item['url']}",
        f"Extrait : {item['extrait']}",
        "",
    ])

lignes.extend([
    "Bonne verification !",
    "Veille Gravel Toulouse",
])

message = EmailMessage()
message["From"] = expediteur
message["To"] = destinataire
message["Subject"] = (
    f"Veille Gravel Toulouse - {len(nouveautes)} nouveaute(s)"
)
message.set_content("\n".join(lignes))

contexte = ssl.create_default_context()

with smtplib.SMTP_SSL(
    "smtp.orange.fr", 465, context=contexte, timeout=30
) as serveur:
    serveur.login(expediteur, mot_de_passe)
    serveur.send_message(message)

print(f"E-mail envoye : {len(nouveautes)} observations.")
