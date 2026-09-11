# Suivi Bourse — architecture client léger + serveur de scoring

Après plusieurs bugs bloquants côté compilation Android de pandas/numpy
(voir historique de la conversation), l'app est maintenant coupée en deux :

- **`server.py`** : petit serveur Flask qui héberge `scoring.py` (la
  logique complète de bilan_scanner.py — santé financière + fiabilité
  dividende, mêmes pondérations). Tourne sur ton PC, ton VPS, ou Termux
  — là où `pip install pandas yfinance` s'installe normalement.
- **App Kivy (`main.py`)** : client léger, ne dépend que de `kivy` et
  `requests`. Appelle le serveur en HTTP pour récupérer les scores.
  Compile en quelques minutes, sans les soucis de recettes numpy/pandas.

## 1. Lancer le serveur

Sur la machine qui va faire tourner `server.py` en continu (ton PC,
un VPS, ou Termux) :

```bash
pip install flask yfinance pandas --break-system-packages
python server.py
```

Le serveur écoute sur `0.0.0.0:8765`. Note l'adresse IP de cette machine
sur ton réseau local (`ip addr` sur Linux, `ipconfig` sur Windows) —
tu en auras besoin dans l'app.

**Pour un accès depuis n'importe où** (pas juste le Wi-Fi de la maison),
le plus simple et sûr est d'installer **Tailscale** (VPN gratuit,
zéro-config) sur le serveur ET sur le téléphone : l'app utilise alors
l'IP Tailscale du serveur, accessible depuis n'importe quel réseau sans
exposer le serveur publiquement sur Internet.

## 2. Configurer l'app

1. Ouvre l'app → **Param.**
2. Renseigne l'adresse : `http://IP-DU-SERVEUR:8765` (ex: `http://192.168.1.42:8765`
   ou l'IP Tailscale)
3. **Tester la connexion** pour vérifier
4. **Enregistrer**

## 3. Utiliser l'app

- **+ Ajouter** : ticker, quantité, PRU
- **Rafraîchir** : interroge le serveur pour tous les tickers du
  portefeuille (prix, PV/MV, notes)
- Tape sur une ligne pour voir le détail (prochain dividende, points clés)

## Fichiers

- `main.py` — app Kivy (client)
- `api_client.py` — appels HTTP vers le serveur
- `storage.py` — positions + paramètres, stockage JSON local sur le téléphone
- `server.py` — serveur Flask (à lancer côté PC/VPS/Termux)
- `scoring.py` — logique de scoring (identique à avant, inchangée)
- `buildozer.spec` — packaging Android (léger : kivy + requests seulement)

## Compiler l'APK

Process GitHub Actions inchangé (voir `PROCESS_BUILD_APK.md`) — mais
cette fois la compilation devrait être nettement plus rapide et fiable
puisqu'il n'y a plus de pandas/numpy à cross-compiler.

## Limitation à garder en tête

Le serveur doit être **allumé et accessible** au moment où tu ouvres
l'app pour que "Rafraîchir" fonctionne. Si tu veux une disponibilité
24/7 sans dépendre de ton PC, héberge `server.py` sur un petit VPS
(quelques euros/mois) plutôt qu'en local.
