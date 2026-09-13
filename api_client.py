#!/usr/bin/env python3
"""
api_client.py

Client HTTP pour interroger server.py (qui héberge la vraie logique de
scoring, avec yfinance/pandas côté PC/VPS). L'app Kivy n'a besoin que de
`requests` — plus de compilation native de pandas/numpy pour Android.
"""

import requests

TIMEOUT_SECONDES = 15


def analyser_position(server_url, ticker, quantite=None, pru=None):
    """
    Appelle GET {server_url}/analyser?ticker=...&quantite=...&pru=...
    Retourne le même dict que scoring.analyser_position(), ou un dict
    d'erreur si le serveur est injoignable.
    """
    params = {"ticker": ticker}
    if quantite is not None:
        params["quantite"] = quantite
    if pru is not None:
        params["pru"] = pru

    url = server_url.rstrip("/") + "/analyser"

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT_SECONDES)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        return _erreur(ticker, "Serveur injoignable — vérifie l'adresse dans Paramètres "
                                "et que server.py tourne bien.")
    except requests.exceptions.Timeout:
        return _erreur(ticker, "Le serveur met trop de temps à répondre (timeout).")
    except Exception as e:
        return _erreur(ticker, str(e))


def obtenir_avis_analystes(server_url, ticker):
    """Appelle GET {server_url}/analystes?ticker=...
    Retourne un dict d'avis analystes ou None si indisponible."""
    url = server_url.rstrip("/") + "/analystes"
    try:
        resp = requests.get(url, params={"ticker": ticker}, timeout=TIMEOUT_SECONDES)
        resp.raise_for_status()
        data = resp.json()
        return data.get("avis_analystes")
    except Exception:
        return None


def obtenir_actualites(server_url, ticker, limit=6):
    """Appelle GET {server_url}/news?ticker=...&limit=...
    Retourne une liste de dicts {titre, editeur, date, lien, sentiment},
    ou une liste vide en cas d'erreur."""
    url = server_url.rstrip("/") + "/news"
    try:
        resp = requests.get(url, params={"ticker": ticker, "limit": limit}, timeout=TIMEOUT_SECONDES)
        resp.raise_for_status()
        data = resp.json()
        return data.get("actualites", [])
    except Exception:
        return []


def obtenir_portefeuille_t212(server_url):
    """Appelle GET {server_url}/t212/portefeuille.
    Retourne (positions, erreur) où positions est une liste de dicts
    {ticker, quantite, pru}, erreur est None si tout s'est bien passé."""
    url = server_url.rstrip("/") + "/t212/portefeuille"
    try:
        resp = requests.get(url, timeout=20)
        data = resp.json()
        if resp.status_code != 200:
            return [], data.get("erreur", f"Erreur serveur ({resp.status_code})")
        return data.get("positions", []), data.get("erreur")
    except requests.exceptions.ConnectionError:
        return [], "Serveur injoignable — vérifie l'adresse dans Paramètres."
    except requests.exceptions.Timeout:
        return [], "Le serveur met trop de temps à répondre (timeout)."
    except Exception as e:
        return [], str(e)


def tester_connexion(server_url):
    """Vérifie que le serveur répond. Retourne (ok: bool, message: str)."""
    url = server_url.rstrip("/") + "/health"
    try:
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return True, "Connexion OK"
        return False, f"Réponse inattendue ({resp.status_code})"
    except requests.exceptions.ConnectionError:
        return False, "Serveur injoignable (vérifie l'adresse et que server.py tourne)"
    except requests.exceptions.Timeout:
        return False, "Timeout — le serveur ne répond pas"
    except Exception as e:
        return False, str(e)


def _erreur(ticker, message):
    return {
        "ticker": ticker,
        "nom": ticker,
        "prix_actuel": None,
        "devise": None,
        "valeur_position": None,
        "pv_mv_eur": None,
        "pv_mv_pct": None,
        "prochain_dividende_date": None,
        "prochain_dividende_montant": None,
        "rendement_pct": None,
        "note_sante": None,
        "note_div": None,
        "notes_sante": [],
        "notes_div": [],
        "verdict": "⚪ Erreur",
        "erreur": message,
    }
