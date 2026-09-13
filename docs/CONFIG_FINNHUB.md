# Configuration Finnhub (avis analystes)

Finnhub fournit une API officielle et documentée (pas de scraping) pour
les recommandations d'analystes et objectifs de cours — plus fiable que
yfinance sur ce point précis. Clé gratuite, 60 appels/min (largement
suffisant vu qu'on ne l'utilise que 2x/jour par ticker).

## Obtenir une clé gratuite

1. Va sur **https://finnhub.io/register**
2. Crée un compte (gratuit, pas de carte bancaire demandée)
3. Ta clé API apparaît directement sur le dashboard après inscription

## Configurer sur le VPS

```bash
python3 -c "
import getpass
cle = getpass.getpass('Colle ta clé Finnhub : ').strip()
with open('finnhub_api_key.txt', 'w') as f:
    f.write(cle)
print('Enregistré, longueur :', len(cle))
"
chmod 600 finnhub_api_key.txt
```

Ajoute-la aussi au `.gitignore` si ce n'est pas déjà fait pour les autres
clés :
```bash
echo "finnhub_api_key.txt" >> .gitignore
```

## Fonctionnement

- Si la clé est configurée : avis analystes détaillés (répartition achat
  fort/achat/conserver/vente/vente forte + objectif de cours) via Finnhub
- Si absente ou si l'appel échoue : repli automatique sur les données
  yfinance déjà récupérées (consensus global uniquement, moins détaillé)
- Mis à jour 2x/jour (même cycle que la santé financière/fiabilité
  dividende), pas à chaque ouverture de l'écran

## Tester

```bash
sudo systemctl restart suivi-bourse
curl "http://localhost:8765/analystes?ticker=AAPL"
```
