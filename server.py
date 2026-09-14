#!/usr/bin/env python3
"""
server.py

Petit serveur HTTP exposant scoring.py (santé financière + fiabilité
dividende, portage de bilan_scanner.py), avec DEUX cycles de mise à jour
distincts pour équilibrer fraîcheur des données et risque de ban Yahoo :

1. PRIX + ACTUALITÉS — rafraîchis toutes les PRIX_REFRESH_SECONDES (15 min
   par défaut). Ce sont les données qui bougent vite et qu'on veut voir
   changer rapidement (ex: détecter qu'une actu vient de faire bouger le
   cours). Le prix est récupéré via fast_info (léger, un seul appel réseau
   minimal) plutôt que la fiche complète.

2. FONDAMENTAUX (santé financière, fiabilité dividende, bilan, cashflow)
   — rafraîchis seulement aux horaires FONDAMENTAUX_HEURES (2x/jour). Ces
   données ne changent pas d'une heure à l'autre, inutile de les
   re-télécharger souvent : ce sont les appels les plus lourds (bilan,
   cashflow, historique de dividendes sur plusieurs années).

Dans tous les cas, l'app cliente ne déclenche JAMAIS d'appel Yahoo
directement : elle lit toujours depuis ce cache, mis à jour uniquement
par les cycles planifiés en tâche de fond.

Lancement :
    pip install flask yfinance pandas curl_cffi --break-system-packages
    python server.py

Endpoints :
    GET /analyser?ticker=MC.PA&quantite=10&pru=650
    GET /news?ticker=MC.PA&limit=6
    GET /health
    GET /etat
"""

import json
import os
import sys
import threading
import time
from datetime import datetime

from flask import Flask, request, jsonify

import scoring
import t212_client

app = Flask(__name__)

# ----------------------------------------------------------------------
# Configuration des deux cycles — ajuste ici selon ton compromis
# fraîcheur / prudence vis-à-vis de Yahoo.
# ----------------------------------------------------------------------
PRIX_REFRESH_SECONDES = 15 * 60       # prix + actus : toutes les 15 min
FONDAMENTAUX_HEURES = ["08:00", "18:00"]  # santé/dividende : 2x/jour

# Une actu est considérée "susceptible d'avoir bougé le cours" si elle est
# parue il y a moins de ce délai ET que son sentiment n'est pas neutre.
FENETRE_ALERTE_ACTU_SECONDES = 2 * PRIX_REFRESH_SECONDES

CACHE_FICHIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")

_lock = threading.Lock()
_cache = {
    "prix": {},           # ticker -> {horodatage, prix_actuel, devise, sma50, sma200, ...}
    "fondamentaux": {},   # ticker -> {horodatage, valeur: résultat complet}
    "news": {},           # ticker -> {horodatage, valeur: [...]}
    "analystes": {},      # ticker -> {horodatage, valeur: {...}}
    "dividendes": {},     # ticker -> {horodatage, valeur: [{date, montant, prevu}, ...]}
    "alertes": {},        # ticker -> [{type, message}, ...] (dernier cycle)
    "tickers_connus": [],
    "derniers_refresh_prix": [],
    "derniers_refresh_fondamentaux": [],
}


def _charger_cache_disque():
    global _cache
    if os.path.exists(CACHE_FICHIER):
        try:
            with open(CACHE_FICHIER, "r", encoding="utf-8") as f:
                with _lock:
                    disque = json.load(f)
                    _cache.update(disque)
        except Exception as e:
            print(f"[cache] lecture impossible, on repart de zéro : {e}", file=sys.stderr)


def _sauver_cache_disque():
    with _lock:
        instantane = json.loads(json.dumps(_cache))
    try:
        with open(CACHE_FICHIER, "w", encoding="utf-8") as f:
            json.dump(instantane, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[cache] écriture impossible : {e}", file=sys.stderr)


class LimiteurDebit:
    def __init__(self, max_appels, fenetre_secondes):
        self.max_appels = max_appels
        self.fenetre = fenetre_secondes
        self._horodatages = []
        self._lock = threading.Lock()

    def attendre_tour(self):
        while True:
            with self._lock:
                maintenant = time.time()
                self._horodatages = [t for t in self._horodatages if maintenant - t < self.fenetre]
                if len(self._horodatages) < self.max_appels:
                    self._horodatages.append(maintenant)
                    return
                attente = self.fenetre - (maintenant - self._horodatages[0])
            time.sleep(max(attente, 0.05))


_limiteur_yfinance = LimiteurDebit(max_appels=2, fenetre_secondes=6)


def _enregistrer_ticker_connu(ticker):
    cle = ticker.strip().upper()
    with _lock:
        if cle not in _cache["tickers_connus"]:
            _cache["tickers_connus"].append(cle)
    return cle


def _rafraichir_news_et_alertes(ticker, prix_info):
    """Cycle léger : actualités + calcul des alertes. Les données prix/
    techniques sont désormais récupérées en lot pour tous les tickers
    d'un coup (voir _cycle_prix_toutes_les_tickers), cette fonction ne
    s'occupe plus que des news (qui ne peuvent pas être batchées, il n'y
    a pas d'équivalent multi-tickers côté yfinance pour les actualités)."""
    cle = _enregistrer_ticker_connu(ticker)

    with _lock:
        entree_fond = _cache["fondamentaux"].get(cle)
    nom_connu = entree_fond["valeur"].get("nom") if entree_fond else None

    _limiteur_yfinance.attendre_tour()
    actualites = scoring.obtenir_actualites(ticker, limit=6, nom_entreprise=nom_connu)

    maintenant = time.time()
    # Ne marque "alerte" que les actus au signal vraiment marqué (force >= 2,
    # ex: 2 mots positifs contre 0 négatif, pas juste 1 contre 0) et parmi
    # celles-ci, seulement les 2 plus pertinentes — pertinence = force du
    # sentiment PONDÉRÉE par la fraîcheur (une actu vieille de 25 min pèse
    # moins qu'une actu vieille de 2 min, même à force égale), plutôt
    # qu'un simple tri par force puis date qui traitait toute la fenêtre
    # de façon binaire. Évite de noyer l'utilisateur sous des alertes
    # faibles ou de sur-pondérer une actu qui a juste eu de la chance sur
    # l'horodatage.
    for item in actualites:
        item["alerte"] = False

    candidates = [
        item for item in actualites
        if item.get("epoch")
        and (maintenant - item["epoch"]) < FENETRE_ALERTE_ACTU_SECONDES
        and item.get("sentiment") in ("positif", "negatif")
        and item.get("force_sentiment", 0) >= 2
    ]
    for item in candidates:
        item["_pertinence"] = item.get("force_sentiment", 0) * _poids_fraicheur(maintenant - item["epoch"])
    candidates.sort(key=lambda x: x["_pertinence"], reverse=True)
    for item in candidates[:2]:
        item["alerte"] = True
    for item in candidates:
        item.pop("_pertinence", None)

    alertes = _calculer_alertes(cle, prix_info, actualites, entree_fond)

    with _lock:
        _cache["news"][cle] = {"horodatage": maintenant, "valeur": actualites}
        _cache["alertes"][cle] = alertes

    return actualites


# ----------------------------------------------------------------------
# Détection d'alertes : croisement SMA200, pic de volume, densité d'actus
# marquées, rappel J-2 avant détachement de dividende.
# ----------------------------------------------------------------------
SEUIL_RATIO_VOLUME = 2.0          # volume actuel > 2x la moyenne = pic
SEUIL_DENSITE_ACTUS_PONDEREE = 2.5  # somme des poids de fraîcheur des actus marquées
FENETRE_DENSITE_ACTUS_SECONDES = 24 * 3600  # sur les dernières 24h


def _poids_fraicheur(age_secondes):
    """Poids décroissant avec l'âge d'une actu, utilisé pour pondérer les
    alertes plutôt que de traiter toute la fenêtre de façon binaire :
    une actu vieille de 20h ne doit pas compter autant qu'une actu vieille
    de 20 min pour déclencher une alerte de densité ou être choisie comme
    "alerte marquante"."""
    if age_secondes <= 3600:            # < 1h : signal quasi intact
        return 1.0
    if age_secondes <= 6 * 3600:        # < 6h
        return 0.7
    if age_secondes <= 12 * 3600:       # < 12h
        return 0.45
    return 0.25                          # jusqu'à 24h (fenêtre max) : signal faible


def _calculer_alertes(cle, prix_info, actualites, entree_fond):
    alertes = []

    # --- Golden Cross / Death Cross (SMA50 vs SMA200) — plus fiable que
    # cours vs SMA200 seul, car confirme un vrai changement de dynamique
    # à moyen terme plutôt qu'une simple variation ponctuelle du cours. ---
    croisement_actuel = prix_info.get("sma50_au_dessus_sma200")
    if croisement_actuel is not None:
        with _lock:
            ancienne_entree = _cache["prix"].get(cle)
        croisement_avant = ancienne_entree.get("sma50_au_dessus_sma200") if ancienne_entree else None
        if croisement_avant is not None and croisement_avant != croisement_actuel:
            if croisement_actuel:
                alertes.append({
                    "type": "golden_cross",
                    "message": "Golden Cross : la SMA50 vient de croiser au-dessus de la SMA200 — signal haussier",
                })
            else:
                alertes.append({
                    "type": "death_cross",
                    "message": "Death Cross : la SMA50 vient de croiser sous la SMA200 — signal baissier",
                })

    # --- Pic de volume ---
    ratio_volume = prix_info.get("ratio_volume")
    if ratio_volume is not None and ratio_volume >= SEUIL_RATIO_VOLUME:
        alertes.append({
            "type": "pic_volume",
            "message": f"Volume anormalement élevé (x{ratio_volume:.1f} vs moyenne)",
        })

    # --- Densité d'actualités marquées, pondérée par fraîcheur : 5 actus
    # marquées étalées sur 24h ne doivent pas peser autant que 5 actus
    # marquées dans la dernière heure — on somme des poids de fraîcheur
    # plutôt que de compter brutalement les occurrences dans la fenêtre. ---
    maintenant = time.time()
    actus_marquees_recentes = [
        a for a in actualites
        if a.get("sentiment") in ("positif", "negatif")
        and a.get("force_sentiment", 0) >= 2
        and a.get("epoch")
        and (maintenant - a["epoch"]) < FENETRE_DENSITE_ACTUS_SECONDES
    ]
    poids_total = sum(_poids_fraicheur(maintenant - a["epoch"]) for a in actus_marquees_recentes)
    if poids_total >= SEUIL_DENSITE_ACTUS_PONDEREE:
        positifs = sum(1 for a in actus_marquees_recentes if a["sentiment"] == "positif")
        negatifs = len(actus_marquees_recentes) - positifs
        alertes.append({
            "type": "densite_actus",
            "message": f"{len(actus_marquees_recentes)} actualités marquées en 24h "
                       f"({positifs} positives / {negatifs} négatives, dont récentes surtout) — "
                       f"signal plus fort qu'une actu isolée",
        })

    # --- Rappel J-2 avant détachement de dividende ---
    if entree_fond:
        date_div = entree_fond["valeur"].get("prochain_dividende_date")
        if date_div:
            try:
                dt_div = datetime.strptime(date_div, "%d/%m/%Y")
                jours_restants = (dt_div.date() - datetime.now().date()).days
                if 0 <= jours_restants <= 2:
                    alertes.append({
                        "type": "dividende_imminent",
                        "message": f"Détachement du dividende dans {jours_restants} jour(s) ({date_div})",
                    })
            except ValueError:
                pass

    return alertes


def _rafraichir_fondamentaux(ticker):
    """Cycle lourd : santé financière + fiabilité dividende (bilan,
    cashflow, historique dividendes) + avis analystes. Ne tourne que
    2x/jour — les avis analystes ne changent pas d'une heure à l'autre."""
    cle = _enregistrer_ticker_connu(ticker)

    _limiteur_yfinance.attendre_tour()
    resultat = scoring.analyser_position(ticker, None, None)

    avis_secours = resultat.get("avis_analystes")
    avis = scoring.obtenir_avis_analystes(ticker, secours=avis_secours)

    with _lock:
        _cache["fondamentaux"][cle] = {"horodatage": time.time(), "valeur": resultat}
        _cache["analystes"][cle] = {"horodatage": time.time(), "valeur": avis}

    return resultat


def _cycle_prix_toutes_les_tickers():
    with _lock:
        tickers = list(_cache["tickers_connus"])
    if not tickers:
        return
    print(f"[cycle prix/technique] téléchargement en lot pour {len(tickers)} ticker(s)...")

    _limiteur_yfinance.attendre_tour()
    donnees_lot = scoring.obtenir_donnees_techniques_lot(tickers)

    maintenant = time.time()
    for t in tickers:
        cle = t.strip().upper()
        info = donnees_lot.get(cle, {})
        if info.get("erreur"):
            print(f"[cycle prix/technique] échec pour {cle} : {info['erreur']}", file=sys.stderr)
            continue

        with _lock:
            entree_fond = _cache["fondamentaux"].get(cle)
            devise_connue = None
            if entree_fond:
                devise_connue = entree_fond["valeur"].get("devise")
            _cache["prix"][cle] = {
                "horodatage": maintenant,
                "prix_actuel": info.get("prix_actuel"),
                "devise": devise_connue,
                "sma50": info.get("sma50"),
                "sma200": info.get("sma200"),
                "au_dessus_sma200": info.get("au_dessus_sma200"),
                "sma50_au_dessus_sma200": info.get("sma50_au_dessus_sma200"),
                "volume_actuel": info.get("volume_actuel"),
                "volume_moyen": info.get("volume_moyen"),
                "ratio_volume": info.get("ratio_volume"),
            }

    print(f"[cycle news/alertes] {len(tickers)} ticker(s)...")
    for t in tickers:
        cle = t.strip().upper()
        with _lock:
            prix_info = _cache["prix"].get(cle, {})
        try:
            _rafraichir_news_et_alertes(t, prix_info)
        except Exception as e:
            print(f"[cycle news/alertes] échec pour {t} : {e}", file=sys.stderr)

    with _lock:
        _cache["derniers_refresh_prix"] = (_cache.get("derniers_refresh_prix") or [])[-9:] + [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
    _sauver_cache_disque()


def _cycle_fondamentaux_toutes_les_tickers():
    with _lock:
        tickers = list(_cache["tickers_connus"])
    print(f"[cycle fondamentaux] {len(tickers)} ticker(s)...")
    for t in tickers:
        try:
            _rafraichir_fondamentaux(t)
        except Exception as e:
            print(f"[cycle fondamentaux] échec pour {t} : {e}", file=sys.stderr)
    with _lock:
        _cache["derniers_refresh_fondamentaux"] = (_cache.get("derniers_refresh_fondamentaux") or [])[-9:] + [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ]
    _sauver_cache_disque()


def _boucle_planificateur():
    dernier_cycle_prix = 0
    derniere_execution_fond = {}

    while True:
        maintenant_ts = time.time()
        maintenant_dt = datetime.now()

        if maintenant_ts - dernier_cycle_prix >= PRIX_REFRESH_SECONDES:
            dernier_cycle_prix = maintenant_ts
            try:
                _cycle_prix_toutes_les_tickers()
            except Exception as e:
                print(f"[planificateur] erreur cycle prix : {e}", file=sys.stderr)

        heure_actuelle = maintenant_dt.strftime("%H:%M")
        jour_actuel = maintenant_dt.strftime("%Y-%m-%d")
        if heure_actuelle in FONDAMENTAUX_HEURES and derniere_execution_fond.get(heure_actuelle) != jour_actuel:
            derniere_execution_fond[heure_actuelle] = jour_actuel
            try:
                _cycle_fondamentaux_toutes_les_tickers()
            except Exception as e:
                print(f"[planificateur] erreur cycle fondamentaux : {e}", file=sys.stderr)

        time.sleep(15)


@app.route("/t212/portefeuille")
def t212_portefeuille():
    positions, erreur = t212_client.obtenir_portefeuille_t212()
    if erreur:
        return jsonify({"erreur": erreur, "positions": []}), 502
    return jsonify({"erreur": None, "positions": positions})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/etat")
def etat():
    with _lock:
        return jsonify({
            "tickers_connus": _cache["tickers_connus"],
            "derniers_refresh_prix": _cache.get("derniers_refresh_prix", []),
            "derniers_refresh_fondamentaux": _cache.get("derniers_refresh_fondamentaux", []),
            "prix_refresh_secondes": PRIX_REFRESH_SECONDES,
            "fondamentaux_heures": FONDAMENTAUX_HEURES,
        })


@app.route("/analyser")
def analyser():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"erreur": "paramètre 'ticker' manquant"}), 400

    quantite = request.args.get("quantite", type=float)
    pru = request.args.get("pru", type=float)
    cle = ticker.strip().upper()

    with _lock:
        entree_fond = _cache["fondamentaux"].get(cle)
        entree_prix = _cache["prix"].get(cle)

    if entree_fond is None:
        # Jamais vu : peuple fondamentaux + prix/technique (lot d'un seul
        # ticker) + déclenche le premier fetch news, pour ne pas laisser
        # l'utilisateur sans rien jusqu'au prochain cycle programmé.
        resultat = _rafraichir_fondamentaux(ticker)
        donnees_lot = scoring.obtenir_donnees_techniques_lot([ticker])
        info = donnees_lot.get(cle, {})
        with _lock:
            _cache["prix"][cle] = {
                "horodatage": time.time(),
                "prix_actuel": info.get("prix_actuel"),
                "devise": resultat.get("devise"),
                "sma50": info.get("sma50"),
                "sma200": info.get("sma200"),
                "au_dessus_sma200": info.get("au_dessus_sma200"),
                "sma50_au_dessus_sma200": info.get("sma50_au_dessus_sma200"),
                "volume_actuel": info.get("volume_actuel"),
                "volume_moyen": info.get("volume_moyen"),
                "ratio_volume": info.get("ratio_volume"),
            }
            entree_prix = _cache["prix"].get(cle)
        _rafraichir_news_et_alertes(ticker, entree_prix)
        _sauver_cache_disque()
    else:
        resultat = dict(entree_fond["valeur"])

    resultat = dict(resultat)

    # Le prix vient toujours du cache "prix" (rafraîchi plus souvent) s'il
    # est disponible — plus frais que celui capturé lors du dernier passage
    # fondamentaux.
    if entree_prix is not None and entree_prix.get("prix_actuel") is not None:
        resultat["prix_actuel"] = entree_prix["prix_actuel"]
        resultat["devise"] = entree_prix.get("devise") or resultat.get("devise")
        resultat["sma50"] = entree_prix.get("sma50")
        resultat["sma200"] = entree_prix.get("sma200")
        resultat["au_dessus_sma200"] = entree_prix.get("au_dessus_sma200")
        resultat["sma50_au_dessus_sma200"] = entree_prix.get("sma50_au_dessus_sma200")
        resultat["volume_actuel"] = entree_prix.get("volume_actuel")
        resultat["volume_moyen"] = entree_prix.get("volume_moyen")
        resultat["ratio_volume"] = entree_prix.get("ratio_volume")

    with _lock:
        resultat["alertes"] = _cache["alertes"].get(cle, [])

    prix = resultat.get("prix_actuel")
    if prix is not None and quantite is not None:
        valeur = prix * quantite
        resultat["valeur_position"] = valeur
        if pru is not None:
            resultat["pv_mv_eur"] = valeur - pru * quantite
            resultat["pv_mv_pct"] = ((prix - pru) / pru * 100) if pru else None

    return jsonify(resultat)


@app.route("/analystes")
def analystes():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"erreur": "paramètre 'ticker' manquant"}), 400
    cle = ticker.strip().upper()

    with _lock:
        entree = _cache["analystes"].get(cle)

    if entree is None:
        # Jamais vu : déclenche un cycle fondamentaux complet (peuple
        # aussi santé/dividende au passage si absent).
        _rafraichir_fondamentaux(ticker)
        _sauver_cache_disque()
        with _lock:
            entree = _cache["analystes"].get(cle)

    avis = entree["valeur"] if entree else None
    return jsonify({"ticker": ticker, "avis_analystes": avis})


def _rafraichir_dividendes(ticker):
    """Calendrier de versements (historique récent + prochain prévu) pour
    un ticker. Cache long (comme fondamentaux) : ça ne change pas d'une
    heure à l'autre, et ça évite de re-télécharger tout l'historique de
    dividendes à chaque ouverture de l'onglet Dividendes."""
    cle = _enregistrer_ticker_connu(ticker)
    _limiteur_yfinance.attendre_tour()
    try:
        ticker_obj = scoring._nouveau_ticker(ticker)
        calendrier = scoring.obtenir_calendrier_dividendes(ticker_obj)
    except Exception as e:
        print(f"[dividendes] échec pour {ticker} : {e}", file=sys.stderr)
        calendrier = []
    with _lock:
        _cache["dividendes"][cle] = {"horodatage": time.time(), "valeur": calendrier}
    return calendrier


@app.route("/dividendes")
def dividendes():
    """GET /dividendes?tickers=AAPL,MC.PA,KO — calendrier de versements
    (déjà versés + prochain prévu) pour chaque ticker du portefeuille.
    Réponse : {"AAPL": [{"date": "2026-11-14", "montant": 0.26, "prevu": true}, ...], ...}
    """
    tickers_brut = request.args.get("tickers", "")
    tickers = [t.strip().upper() for t in tickers_brut.split(",") if t.strip()]
    resultat = {}
    for ticker in tickers:
        cle = ticker.strip().upper()
        with _lock:
            entree = _cache["dividendes"].get(cle)
        if entree is None:
            calendrier = _rafraichir_dividendes(ticker)
            _sauver_cache_disque()
        else:
            calendrier = entree["valeur"]
        resultat[ticker] = calendrier
    return jsonify(resultat)


@app.route("/news")
def news():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"erreur": "paramètre 'ticker' manquant"}), 400
    cle = ticker.strip().upper()

    with _lock:
        entree = _cache["news"].get(cle)

    if entree is None:
        with _lock:
            prix_info = _cache["prix"].get(cle, {})
        actualites = _rafraichir_news_et_alertes(ticker, prix_info)
        _sauver_cache_disque()
    else:
        actualites = entree["valeur"]

    return jsonify({"ticker": ticker, "actualites": actualites})


if __name__ == "__main__":
    _charger_cache_disque()

    threading.Thread(target=_boucle_planificateur, daemon=True).start()
    print(f"[planificateur] prix/news toutes les {PRIX_REFRESH_SECONDES}s, "
          f"fondamentaux à {FONDAMENTAUX_HEURES}")

    try:
        app.run(host="0.0.0.0", port=8765, debug=False, threaded=True)
    except Exception as e:
        print(f"ERREUR FATALE du serveur : {e}", file=sys.stderr)
        sys.exit(1)
