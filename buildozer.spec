[app]
title = Suivi Bourse
package.name = suivibourse
package.domain = org.yannick

source.dir = .
source.include_exts = py,kv,png,jpg,atlas

version = 0.1

# ATTENTION : yfinance dépend de pandas + requests + numpy, lourds à
# compiler pour Android (voir README.md pour les limites réelles).
requirements = python3,kivy==2.3.0,yfinance,pandas==1.5.3,numpy==1.23.2,requests,certifi

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a

[buildozer]
log_level = 2
warn_on_root = 1
