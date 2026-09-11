# Suivi Bourse — app Kivy de suivi de positions

Formulaire d'ajout manuel (ticker, quantité, PRU), PV/MV en direct, prochain
détachement de dividende, et notes santé financière / fiabilité dividende
**reprenant exactement les critères et pondérations de `bilan_scanner.py`**
(portage sans print dans `scoring.py`).

## Fichiers

- `main.py` — l'app Kivy (3 écrans : portefeuille, ajout, détail)
- `scoring.py` — logique de notation portée de bilan_scanner.py
- `storage.py` — persistance JSON locale des positions
- `buildozer.spec` — config de packaging Android

## Tester tout de suite (PC ou Termux, sans compiler d'APK)

```bash
pip install kivy yfinance --break-system-packages
python main.py
```

Sur Termux, Kivy s'affiche via un serveur X (ex: appli Termux:X11) — pas
d'affichage graphique natif dans Termux seul. C'est le moyen le plus rapide
de valider que la logique et l'UI fonctionnent avant de packager.

## ⚠️ Point d'attention important : compiler l'APK

Buildozer (l'outil qui transforme ce code en `.apk`) a besoin du SDK/NDK
Android, de plusieurs Go d'espace, et compile des paquets natifs. Sur ton
téléphone (pas d'admin, réseau restreint) ce sera probablement difficile
voire impossible à faire tourner directement dans Termux. En pratique :

1. **Le plus simple** : compile l'APK sur un PC (Linux ou WSL) avec
   Buildozer, puis transfère juste le `.apk` sur ton téléphone pour
   l'installer. C'est l'usage normal de Buildozer.
2. **Alternative cloud** : GitHub Actions peut compiler l'APK pour toi
   (action `buildozer` officielle) — tu pousses le code sur ton Gitea/GitHub,
   l'action te sort un `.apk` téléchargeable, aucune compilation locale.
3. `yfinance` embarque `pandas`/`numpy` : recipes p4a disponibles mais la
   compilation est longue (30-60 min) et parfois fragile selon les versions.
   Si ça bloque, la solution de repli est de garder la logique `scoring.py`
   côté **serveur** (petite API Flask sur ton VPS ou en local Termux) et de
   faire une app Kivy plus légère qui l'appelle en HTTP — je peux basculer
   là-dessus si le build Android coince.

## Prochaines étapes possibles

- Icône et splash screen
- Notification (ntfy.sh, vu que tu explores déjà ça) le jour de détachement
  d'un dividende
- Export CSV du portefeuille
- Tri de la liste (par PV/MV, par note, alphabétique)

Dis-moi si tu veux que j'ajoute une de ces briques, ou si tu préfères qu'on
bascule sur l'option "scoring côté VPS + app légère" pour sécuriser le build.
