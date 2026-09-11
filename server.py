#!/usr/bin/env python3
"""
server.py

Petit serveur HTTP exposant scoring.py (santé financière + fiabilité
dividende, portage de bilan_scanner.py). Fait tourner ça sur ton PC, ton
VPS, ou en tâche de fond dans Termux — là où pip install yfinance/pandas
fonctionne normalement (pas de cross-compilation Android à gérer).

L'app Kivy (client léger, sans pandas/numpy) appelle cette API en HTTP.

Lancement :
    pip install flask yfinance pandas --break-system-packages
    python server.py
    # écoute sur 0.0.0.0:8765 par défaut

Endpoints :
    GET /analyser?ticker=MC.PA&quantite=10&pru=650
        -> JSON avec toutes les infos (prix, PV/MV, notes, points clés...)
    GET /health
        -> {"status": "ok"} — pour vérifier que le serveur répond

Sécurité : ce serveur est prévu pour un usage personnel sur réseau local
ou VPN (Tailscale, WireGuard...). Il n'a pas d'authentification — ne
l'expose pas directement sur Internet sans en ajouter une (reverse proxy
+ clé API, par exemple).
"""

from flask import Flask, request, jsonify
import scoring

app = Flask(__name__)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/analyser")
def analyser():
    ticker = request.args.get("ticker")
    if not ticker:
        return jsonify({"erreur": "paramètre 'ticker' manquant"}), 400

    quantite = request.args.get("quantite", type=float)
    pru = request.args.get("pru", type=float)

    resultat = scoring.analyser_position(ticker, quantite, pru)
    return jsonify(resultat)


if __name__ == "__main__":
    # host="0.0.0.0" pour être accessible depuis le téléphone sur le même
    # réseau (ou via Tailscale/VPN) — pas juste depuis localhost.
    app.run(host="0.0.0.0", port=8765, debug=False)
