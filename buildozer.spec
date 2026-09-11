[app]
title = Suivi Bourse
package.name = suivibourse
package.domain = org.yannick

source.dir = .
source.include_exts = py,kv,png,jpg,atlas

version = 0.1

# Client léger : la logique de scoring (yfinance/pandas/numpy) tourne côté
# serveur (voir server.py) — plus besoin de compiler ces dépendances pour
# Android, ce qui évite tous les problèmes de recettes python-for-android.
requirements = python3==3.11.9,hostpython3==3.11.9,kivy==2.3.0,requests,certifi

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
