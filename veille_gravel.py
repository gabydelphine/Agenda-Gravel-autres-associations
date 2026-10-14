
def inventaire_valide(observations):
    """Conserve l'inventaire, les décisions et les événements historiques.

    Seules les observations ayant une date, un lieu géographiquement vérifié
    et un titre identifiable deviennent des fiches à vérifier.
    """
    chemin = Path("inventaire-gravel.json")
    if chemin.exists():
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        if not isinstance(brut, dict) or not isinstance(brut.get("events"), list):
            raise ValueError("Format de l'inventaire incorrect : arrêt sans écrasement")
        fiches = brut["events"]
    else:
        fiches = []

    # Les décisions humaines existantes sont prioritaires sur les sources.
    def cle(f):
        return (f.get("date", ""), normaliser(f.get("title", "")))

    # Fusionner les entrées publiées sans modifier les décisions déjà enregistrées.
    if Path("events.json").exists():
        calendrier = json.loads(Path("events.json").read_text(encoding="utf-8"))
        if not isinstance(calendrier, dict) or not isinstance(calendrier.get("events"), list):
            raise ValueError("Format du calendrier incorrect : arrêt sans écrasement")
        publiees = calendrier["events"]
    else:
        publiees = []

    index = {cle(f): f for f in fiches}
    for f in publiees:
        if "occitanie 600" in normaliser(f.get("title", "")):
            continue
        if cle(f) not in index:
            nouvelle = dict(f)
            nouvelle.setdefault("statut", "valide")
            fiches.append(nouvelle)
            index[cle(nouvelle)] = nouvelle

    for jour, titre, lieu, details, source in MANIFESTATIONS_VALIDEES:
        if date.fromisoformat(jour) < date.today() - timedelta(days=365):
            continue
        entree = {"date": jour, "title": titre, "location": lieu,
                  "details": ("Parcours gravel confirmé par l'organisateur / utilisateur. " + details).strip(),
                  "source": source, "validation_gravel": "utilisateur",
                  "statut": "annule" if "Annulé" in details else "valide"}
        if cle(entree) not in index:
            fiches.append(entree)
            index[cle(entree)] = entree

    # Toutes les sources peuvent alimenter l'inventaire si la fiche est fiable.
    # Ne jamais inventer une date, une commune ou un titre depuis un PDF ambigu.
    # Les observations incomplètes restent dans veille-resultats.json.
    for item in observations:
        jour = item.get("date_evenement")
        lieu = item.get("lieu")
        if not jour or not lieu or item.get("controle_zone") != "dans_zone":
            continue
        try:
            date.fromisoformat(jour)
        except (TypeError, ValueError):
            continue

        titre = (item.get("titre") or "").strip()
        if not titre and item.get("source") == "Velo Occitanie":
            match = re.match(
                r"\s*Détails\s+(.+?)\s+"
                r"(?:lun|mar|mer|jeu|ven|sam|dim)\.?\s+"
                r"\d{1,2}\s+[a-zéèêûôàîïù]+\s+20\d{2}",
                item.get("extrait", ""), re.I | re.S,
            )
            if match:
                titre = match.group(1).strip()

        # Le titre et la commune doivent être explicites et cohérents.
        if not titre or len(titre) > 120 or len(lieu) > 65:
            continue
        titre_n = normaliser(titre)
        if ("occitanie 600" in titre_n or "ogravel" in titre_n
                or "o gravel" in titre_n or "ô gravel" in titre.lower()):
            continue
        if not re.search(r"\b(?:gravel|gravier)\b", normaliser(item.get("extrait", ""))):
            continue

        entree = {
            "date": jour,
            "title": titre,
            "location": lieu,
            "details": "Parcours gravel à vérifier avant publication",
            "source": item.get("url", ""),
            "controle_zone": "dans_zone",
            "distance_toulouse_km": item.get("distance_toulouse_km"),
            "statut": "a_verifier",
        }
        # Les fiches historiques et les décisions humaines sont prioritaires.
        # Pour une même date, on évite aussi les variantes du même intitulé.
        cle_nouvelle = cle(entree)
        if cle_nouvelle in index:
            continue
        doublon = any(
            f.get("date") == jour and (
                titre_n in normaliser(f.get("title", ""))
                or normaliser(f.get("title", "")) in titre_n
            )
            for f in fiches if f.get("title")
        )
        if doublon:
            continue
        fiches.append(entree)
        index[cle_nouvelle] = entree

    # Ne supprimer aucune décision ni aucun événement historique ici.
    # L'affichage public applique déjà la fenêtre glissante de 12 mois.
    fiches.sort(key=lambda f: (f.get("date", ""), f.get("title", "")))
    chemin.write_text(json.dumps({"updated": date.today().isoformat(), "events": fiches},
                                  ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Inventaire préservé : {len(fiches)} fiches (events.json inchangé)")
