#!/usr/bin/env python3
"""Enregistre une décision humaine sur une fiche de l'inventaire gravel.

Utilisation : python validation_gravel.py --id IDENTIFIANT --decision valider|refuser
Le script ne décide rien seul : un workflow GitHub devra vérifier l'auteur
et transmettre explicitement ces arguments après confirmation.
"""
import argparse
import hashlib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

INVENTAIRE = Path('inventaire-gravel.json')
CALENDRIER = Path('events.json')


def normaliser(texte):
    texte = unicodedata.normalize('NFKD', str(texte or ''))
    texte = ''.join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texte).strip().casefold()


def identifiant(fiche):
    base = '|'.join(normaliser(fiche.get(champ, '')) for champ in ('date', 'title', 'location'))
    return hashlib.sha256(base.encode('utf-8')).hexdigest()[:16]


def charger(chemin):
    donnees = json.loads(chemin.read_text(encoding='utf-8'))
    if not isinstance(donnees, dict) or not isinstance(donnees.get('events'), list):
        raise ValueError(f'Format incorrect : {chemin}')
    return donnees


def sauvegarder(chemin, donnees):
    chemin.write_text(json.dumps(donnees, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='Valider ou refuser une manifestation gravel')
    parser.add_argument('--id', required=True, help='Identifiant de la manifestation (16 caractères)')
    parser.add_argument('--decision', required=True, choices=('valider', 'refuser'))
    args = parser.parse_args()

    if not re.fullmatch(r'[a-f0-9]{16}', args.id):
        parser.error('Identifiant incorrect')

    inventaire = charger(INVENTAIRE)
    correspondances = [f for f in inventaire['events'] if identifiant(f) == args.id]
    if len(correspondances) != 1:
        raise SystemExit('Manifestation introuvable ou identifiant ambigu : aucune modification')
    fiche = correspondances[0]
    statut = normaliser(fiche.get('statut', 'a_verifier'))
    if statut == 'annule':
        raise SystemExit('Manifestation annulée : décision bloquée pour préserver l’historique')
    if statut in ('valide', 'refuse'):
        raise SystemExit(f'Décision déjà enregistrée ({statut}) : aucune modification')
    if not fiche.get('date') or not fiche.get('title') or not fiche.get('location'):
        raise SystemExit('Fiche incomplète : aucune modification')
    date.fromisoformat(fiche['date'])
    if 'occitanie 600' in normaliser(fiche['title']) or 'ogravel' in normaliser(fiche.get('organizer', '')):
        raise SystemExit('Événement ÔGravel exclu du calendrier des autres associations')

    if args.decision == 'valider':
        calendrier = charger(CALENDRIER)
        deja = any(identifiant(e) == args.id for e in calendrier['events'])
        if not deja:
            evenement = {cle: fiche[cle] for cle in ('date', 'title', 'location', 'details', 'source', 'end_date') if cle in fiche}
            evenement['statut'] = 'valide'
            calendrier['events'].append(evenement)
            calendrier['events'].sort(key=lambda e: (e.get('date', ''), e.get('title', '')))
            calendrier['updated'] = date.today().isoformat()
            sauvegarder(CALENDRIER, calendrier)
        fiche['statut'] = 'valide'
    else:
        fiche['statut'] = 'refuse'

    fiche['date_decision'] = date.today().isoformat()
    inventaire['updated'] = date.today().isoformat()
    sauvegarder(INVENTAIRE, inventaire)
    print(f"Décision enregistrée : {args.decision} — {fiche['title']}")


if __name__ == '__main__':
    main()
