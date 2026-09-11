#!/usr/bin/env python3
"""
storage.py

Persistance simple des positions du portefeuille dans un fichier JSON local
(app_storage_path()/positions.json — dossier privé de l'app sur Android via
Kivy/plyer, ~/.suivi_bourse/ en dev desktop). Pas de base de données : le
volume attendu (watchlist ~83 titres max) ne le justifie pas.
"""

import json
import os

try:
    from kivy.app import App
    _HAS_KIVY = True
except ImportError:
    _HAS_KIVY = False


def _data_dir():
    """Dossier de données de l'app (privé sur Android, ~/.suivi_bourse sinon)."""
    if _HAS_KIVY:
        app = App.get_running_app()
        if app is not None:
            try:
                d = app.user_data_dir
                os.makedirs(d, exist_ok=True)
                return d
            except Exception:
                pass
    d = os.path.expanduser("~/.suivi_bourse")
    os.makedirs(d, exist_ok=True)
    return d


def _positions_path():
    return os.path.join(_data_dir(), "positions.json")


def charger_positions():
    """Retourne la liste des positions : [{ticker, quantite, pru, date_achat}, ...]."""
    path = _positions_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def sauvegarder_positions(positions):
    path = _positions_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def ajouter_position(ticker, quantite, pru, date_achat=None):
    positions = charger_positions()
    positions.append({
        "ticker": ticker.strip().upper(),
        "quantite": float(quantite),
        "pru": float(pru),
        "date_achat": date_achat or "",
    })
    sauvegarder_positions(positions)
    return positions


def supprimer_position(index):
    positions = charger_positions()
    if 0 <= index < len(positions):
        positions.pop(index)
        sauvegarder_positions(positions)
    return positions


def modifier_position(index, ticker=None, quantite=None, pru=None, date_achat=None):
    positions = charger_positions()
    if 0 <= index < len(positions):
        if ticker is not None:
            positions[index]["ticker"] = ticker.strip().upper()
        if quantite is not None:
            positions[index]["quantite"] = float(quantite)
        if pru is not None:
            positions[index]["pru"] = float(pru)
        if date_achat is not None:
            positions[index]["date_achat"] = date_achat
        sauvegarder_positions(positions)
    return positions
