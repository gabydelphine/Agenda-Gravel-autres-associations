import json
import re
import unicodedata
from datetime import date
from pathlib import Path

CALENDRIER = Path('events.json')
OBSERVATIONS = Path('veille-resultats.json')
SOURCE_PDF = ('https://www.cyclotourisme-31.com/'
              'uploaded/Circuits_InterClubs/2026/'
              'saubensgravel2026.pdf')


def normaliser(texte):
    texte = unicodedata.normalize('NFKD', str(texte))
    texte = ''.join(c for c in texte if not unicodedata.combining(c))
    texte = re.sub(r'\b(\d+)\s*(?:eme|eme|er|e)\b', r'\1', texte, flags=re.I)
    return re.sub(r'\s+', ' ', texte).strip().casefold()


def evenement_existant(evenements, nouveau):
    titre = normaliser(nouveau['title'])
    for evenement in evenements:
        if evenement.get('date') != nouveau['date']:
            continue
        ancien = normaliser(evenement.get('title', ''))
        if ancien and (titre == ancien or titre in ancien or ancien in titre):
            return True
    return False


def analyser_observation(observation):
    extrait = observation.get('extrait', '')
    texte = normaliser(extrait)
    date_evenement = observation.get('date_evenement')

    if (observation.get('url') == SOURCE_PDF
            and 'saubenoise' in texte and 'gravel' in texte):
        return {
            'date': '2026-10-18',
            'title': 'La Ronde Saubenoise',
            'location': 'Saubens (31)',
            'details': 'Gravel : 47 km · 721 m D+',
            'source': SOURCE_PDF,
        }

    if (observation.get('source') != 'Velo Occitanie'
            or not date_evenement
            or not re.search(r'\bgravel\b', extrait, re.I)):
        return None

    # Refuser les anciens extraits mêlant plusieurs manifestations.
    correspondance = re.fullmatch(
        r'\s*Détails\s+(.+?)\s+'
        r'(?:lun|mar|mer|jeu|ven|sam|dim)\.?\s+'
        r'\d{1,2}\s+[a-zéèêûôàîïù]+\s+20\d{2}\s+'
        r'(.*)', extrait, re.I | re.S,
    )
    if not correspondance:
        return None

    titre = correspondance.group(1).strip()
    bloc = correspondance.group(2).strip()
    lieu = re.sub(r'\s+', ' ', str(observation.get('lieu') or '')).strip()

    # La mention Gravel doit appartenir à cette annonce, pas à la suivante.
    if ('détails' in normaliser(bloc)
            or not re.search(r'\bGravel\s+\d+\s+parcours?\b', bloc, re.I)):
        return None

    if (not titre or len(titre) > 100 or not lieu
            or len(lieu) > 60
            or re.search(r'\b(?:parcours|détails|inscription)\b', lieu, re.I)):
        print(f'Annonce incomplete : {titre}')
        return None

    # Vérifier que le lieu correspond réellement au texte de l'annonce.
    if normaliser(lieu) not in normaliser(bloc):
        return None

    try:
        date.fromisoformat(date_evenement)
    except (ValueError, TypeError):
        return None

    return {
        'date': date_evenement,
        'title': titre,
        'location': lieu,
        'details': 'Parcours Gravel annoncé',
        'source': observation.get('url', ''),
    }


if not CALENDRIER.exists() or not OBSERVATIONS.exists():
    raise SystemExit('Fichier calendrier ou veille introuvable')

calendrier = json.loads(CALENDRIER.read_text(encoding='utf-8'))
donnees = json.loads(OBSERVATIONS.read_text(encoding='utf-8'))
evenements = calendrier.get('events', [])
ajouts = 0

for observation in donnees.get('observations', []):
    nouveau = analyser_observation(observation)
    if nouveau is None:
        continue
    try:
        jour = date.fromisoformat(nouveau['date'])
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

evenements.sort(key=lambda e: e.get('date', ''))
if ajouts:
    calendrier['events'] = evenements
    calendrier['updated'] = date.today().isoformat()
    CALENDRIER.write_text(
        json.dumps(calendrier, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )

