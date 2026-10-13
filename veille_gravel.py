
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from html import unescape

SOURCES = {
    "FSGT 31": "https://cyclismefsgt31.fr/",
    "FFVelo 31": "https://haute-garonne.ffvelo.fr/",
    "Cyclotourisme 31": "https://www.cyclotourisme-31.com/",
    "FFVelo 82": "https://cyclotourisme82-ffct.fr/",
    "Velo Occitanie": "https://www.velo-occitanie.fr/",
    "Saubens Cyclo": "https://saubenscyclo31.fr/",
}

MOTS_CLES = (
    "gravel", "grvl", "graviers",
    "ronde saubenoise", "la plaisancoise",
    "raid du lys", "la mailhocoise",
)

fichier = Path("veille-resultats.json")

if fichier.exists():
    donnees = json.loads(fichier.read_text(encoding="utf-8"))
else:
    donnees = {"observations": []}

observations = donnees["observations"]
connus = {item["source"] + item["extrait"]
          for item in observations}

for nom, url in SOURCES.items():
    try:
        requete = Request(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urlopen(requete, timeout=20) as reponse:
            contenu = reponse.read().decode(
                "utf-8", errors="replace"
            )

        texte = re.sub(r"<[^>]+>", " ", contenu)
        texte = unescape(re.sub(r"\s+", " ", texte))

        for mot in MOTS_CLES:
            for correspondance in re.finditer(
                re.escape(mot), texte, re.IGNORECASE
            ):
                debut = max(0, correspondance.start() - 100)
                fin = min(len(texte), correspondance.end() + 160)
                extrait = texte[debut:fin].strip()
                identifiant = nom + extrait

                if identifiant not in connus:
                    observations.append({
                        "source": nom,
                        "url": url,
                        "extrait": extrait,
                        "date_detection": datetime.now(
                            timezone.utc
                        ).date().isoformat(),
                        "statut": "a_verifier"
                    })
                    connus.add(identifiant)

        print(f"Source consultee : {nom}")

    except Exception as erreur:
        print(f"Erreur pour {nom} : {erreur}")

fichier.write_text(
    json.dumps(
        {"observations": observations},
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)

print(f"Total observations : {len(observations)}")

 - name: Sauvegarder l'historique des emails
        if: success() && (github.event.schedule == '0 7 * * 0' || github.event_name == 'workflow_dispatch')
        run: |
          if [ -f veille-notifications.json ]; then
            git config user.name "github-actions[bot]"
            git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
            git add veille-notifications.json
            if ! git diff --cached --quiet; then
              git commit -m "Mise a jour historique notifications gravel"
              git push
            fi
          fi
