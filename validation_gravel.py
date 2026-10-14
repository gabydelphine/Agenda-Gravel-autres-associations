#!/usr/bin/env python3
"""Applique une décision humaine à une fiche de l'inventaire gravel.

Usage : python validation_gravel.py --id IDENTIFIANT --decision valider|refuser
L'autorisation de l'auteur de la décision est contrôlée par le workflow GitHub.
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


def est_ogravel(fiche):
    champs = ' '.join(str(fiche.get(k, '')) for k in ('title', 'organizer', 'organisateur', 'association'))
    urls = ' '.join(str(fiche.get(k, '')) for k in ('source', 'event_url', 'url', 'organizer_url'))
    return bool(re.search(r'\b(?:ogravel|o\s*gravel|occitanie 600)\b', normaliser(champs))
                or re.search(r'https?://(?:[^/]*\.)?ogravel\.com(?:/|$)', urls, re.I))


def main():
    parser = argparse.ArgumentParser(description='Valider ou refuser une manifestation gravel')
    parser.add_argument('--id', required=True)
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
    if statut in ('annule', 'annulee', 'valide', 'refuse'):
        raise SystemExit(f'Fiche déjà traitée ou annulée ({statut}) : aucune modification')
    if statut not in ('a_verifier', 'a verifier', ''):
        raise SystemExit(f'Statut non reconnu ({statut}) : aucune modification')
    if not all(fiche.get(c) for c in ('date', 'title', 'location')):
        raise SystemExit('Fiche incomplète : aucune modification')
    date.fromisoformat(fiche['date'])
    if est_ogravel(fiche):
        raise SystemExit('Manifestation ÔGravel : aucune modification')

    if args.decision == 'valider':
        # Pour publier, une vérification géographique positive est obligatoire.
        if fiche.get('controle_zone') != 'dans_zone':
            raise SystemExit('Zone non confirmée dans les 150 km : publication bloquée')
        distance = fiche.get('distance_toulouse_km')
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not 0 <= distance <= 150:
            raise SystemExit('Distance absente ou hors rayon : publication bloquée')
        calendrier = charger(CALENDRIER)
        # Empêcher les doublons, même si l'intitulé diffère légèrement.
        if any(identifiant(e) == args.id for e in calendrier['events']):
            raise SystemExit('Manifestation déjà publiée : aucune modification')
        evenement = {k: fiche[k] for k in ('date', 'title', 'location', 'details', 'source', 'end_date') if k in fiche}
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
