#!/usr/bin/env python3
"""
scoring.py

Portage de la logique de bilan_scanner.py (analyse santé financière +
fiabilité dividende) pour une utilisation dans l'app Kivy de suivi de
positions. Mêmes critères, mêmes poids, même méthode de normalisation
sur 10 — mais sans aucun print() : chaque fonction retourne un dict
structuré (score, liste de points clés, données brutes) exploitable par
l'interface.

Ne dépend que de yfinance. Peut aussi être utilisé en ligne de commande
pour debug rapide : python scoring.py MC.PA
"""

import os
import sys
from datetime import datetime, timedelta

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None

# yfinance scrape le site Yahoo Finance (pas une API officielle) : Yahoo
# détecte et bloque les requêtes qui ressemblent à du scraping, surtout
# depuis des IPs de datacenter/VPS — indépendamment d'un quota horaire.
# curl_cffi fait passer les requêtes avec une empreinte TLS de vrai
# navigateur Chrome, ce qui réduit fortement les blocages (recommandation
# officielle des mainteneurs de yfinance). Fallback silencieux si absent.
_SESSION_IMPERSONATE = None
if yf is not None:
    try:
        from curl_cffi import requests as _curl_requests
        # "chrome110" est la cible confirmée fonctionnelle avec les
        # versions de curl_cffi limitées à Python 3.7 (ex: 0.5.x).
        # Note : Session(impersonate=...) ne valide PAS la cible à la
        # création — l'erreur n'apparaît qu'à la première vraie requête —
        # donc pas de moyen fiable de tester plusieurs cibles en cascade
        # ici sans faire un vrai appel réseau à chaque tentative.
        _SESSION_IMPERSONATE = _curl_requests.Session(impersonate="chrome110")
    except ImportError:
        _SESSION_IMPERSONATE = None


def _nouveau_ticker(ticker_symbol):
    """Crée un objet yfinance.Ticker, avec impersonation navigateur si
    curl_cffi est disponible (réduit fortement les blocages Yahoo)."""
    if _SESSION_IMPERSONATE is not None:
        return yf.Ticker(ticker_symbol, session=_SESSION_IMPERSONATE)
    return yf.Ticker(ticker_symbol)


_FINNHUB_CLE_FICHIER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "finnhub_api_key.txt")
_CACHE_CLE_FINNHUB = None


def _lire_cle_finnhub():
    global _CACHE_CLE_FINNHUB
    if _CACHE_CLE_FINNHUB is not None:
        return _CACHE_CLE_FINNHUB or None
    if not os.path.exists(_FINNHUB_CLE_FICHIER):
        _CACHE_CLE_FINNHUB = ""
        return None
    try:
        with open(_FINNHUB_CLE_FICHIER, "r", encoding="utf-8") as f:
            _CACHE_CLE_FINNHUB = f.read().strip()
    except Exception:
        _CACHE_CLE_FINNHUB = ""
    return _CACHE_CLE_FINNHUB or None


def _avis_analystes_finnhub(ticker_symbol):
    """Avis analystes via l'API officielle Finnhub (gratuite avec clé,
    non-scraping, plus fiable que yfinance sur ce point précis).
    Retourne None si pas de clé configurée ou si l'appel échoue —
    le serveur fera alors un repli sur les données yfinance."""
    cle = _lire_cle_finnhub()
    if not cle:
        return None

    try:
        import requests
        resp_reco = requests.get(
            "https://finnhub.io/api/v1/stock/recommendation",
            params={"symbol": ticker_symbol, "token": cle}, timeout=10,
        )
        resp_reco.raise_for_status()
        tendances = resp_reco.json()
    except Exception:
        return None

    if not tendances:
        return None

    plus_recent = tendances[0]  # Finnhub trie du plus récent au plus ancien
    strong_buy = plus_recent.get("strongBuy", 0)
    buy = plus_recent.get("buy", 0)
    hold = plus_recent.get("hold", 0)
    sell = plus_recent.get("sell", 0)
    strong_sell = plus_recent.get("strongSell", 0)
    total = strong_buy + buy + hold + sell + strong_sell

    # --- Tendance du consensus vs la période précédente (souvent plus
    # informatif qu'une photo instantanée : un consensus "Achat" qui se
    # dégrade mois après mois est un signal différent d'un consensus
    # "Achat" stable depuis un an). ---
    tendance_label = None
    tendance_delta = None
    if len(tendances) > 1:
        precedent = tendances[1]
        score_actuel = _score_consensus(plus_recent)
        score_precedent = _score_consensus(precedent)
        tendance_delta = score_actuel - score_precedent
        periode_precedente = precedent.get("period", "période précédente")
        if tendance_delta > 0:
            tendance_label = f"Consensus en amélioration vs {periode_precedente}"
        elif tendance_delta < 0:
            tendance_label = f"Consensus en dégradation vs {periode_precedente}"
        else:
            tendance_label = f"Consensus stable vs {periode_precedente}"

    prix_cible = None
    try:
        import requests
        resp_cible = requests.get(
            "https://finnhub.io/api/v1/stock/price-target",
            params={"symbol": ticker_symbol, "token": cle}, timeout=10,
        )
        if resp_cible.status_code == 200:
            prix_cible = resp_cible.json()
    except Exception:
        pass

    return {
        "source": "Finnhub",
        "periode": plus_recent.get("period"),
        "nb_analystes": total,
        "strong_buy": strong_buy,
        "buy": buy,
        "hold": hold,
        "sell": sell,
        "strong_sell": strong_sell,
        "prix_cible_moyen": (prix_cible or {}).get("targetMean"),
        "prix_cible_haut": (prix_cible or {}).get("targetHigh"),
        "prix_cible_bas": (prix_cible or {}).get("targetLow"),
        "tendance": tendance_label,
        "tendance_delta": tendance_delta,
    }


def _score_consensus(tendance):
    """Score simple d'un point de consensus Finnhub : (achats) - (ventes),
    utilisé pour comparer deux périodes et détecter une tendance."""
    return (
        (tendance.get("strongBuy", 0) + tendance.get("buy", 0))
        - (tendance.get("sell", 0) + tendance.get("strongSell", 0))
    )


def obtenir_avis_analystes(ticker_symbol, secours=None):
    """Avis analystes : Finnhub en priorité (API officielle), repli sur
    les champs déjà extraits de yfinance (paramètre `secours`, calculés
    par analyser_position) si Finnhub n'est pas configuré ou échoue."""
    resultat_finnhub = _avis_analystes_finnhub(ticker_symbol)
    if resultat_finnhub:
        return resultat_finnhub

    if secours:
        return {
            "source": "Yahoo Finance (repli)",
            "periode": None,
            "nb_analystes": secours.get("nb_analystes"),
            "consensus": secours.get("consensus"),
            "prix_cible_moyen": secours.get("prix_cible_moyen"),
            "prix_cible_haut": secours.get("prix_cible_haut"),
            "prix_cible_bas": secours.get("prix_cible_bas"),
        }

    return None


def obtenir_donnees_techniques_lot(tickers_liste):
    """Récupère prix + historique pour PLUSIEURS tickers en un seul appel
    yf.download() (endpoint "chart", généralement plus tolérant que
    l'endpoint "quote" utilisé par fast_info/.info). Calcule nous-mêmes
    SMA50/SMA200/volume moyen à partir de l'historique réel plutôt que de
    dépendre de champs pré-calculés par Yahoo (parfois absents/obsolètes).

    Retourne un dict {ticker: {prix_actuel, devise, sma50, sma200,
    au_dessus_sma200, volume_actuel, volume_moyen, ratio_volume, erreur}}.

    Note : ça reste une requête Yahoo par ticker en interne (pas un vrai
    batch réseau) — le gain est la fiabilité de l'endpoint et la précision
    du calcul, pas une réduction du nombre d'appels.
    """
    resultats = {t.strip().upper(): {"prix_actuel": None, "devise": None, "erreur": "non traité"}
                 for t in tickers_liste}

    if yf is None or not tickers_liste:
        for cle in resultats:
            resultats[cle]["erreur"] = "yfinance non installé"
        return resultats

    try:
        kwargs = dict(
            tickers=" ".join(tickers_liste),
            period="220d",
            interval="1d",
            group_by="ticker",
            threads=False,   # séquentiel : reste compatible avec un limiteur de débit externe
            progress=False,
            auto_adjust=False,
        )
        if _SESSION_IMPERSONATE is not None:
            kwargs["session"] = _SESSION_IMPERSONATE
        df = yf.download(**kwargs)
    except Exception as e:
        for cle in resultats:
            resultats[cle]["erreur"] = str(e)
        return resultats

    # Les versions récentes de yfinance renvoient des colonnes multi-niveaux
    # (MultiIndex) avec group_by="ticker" même pour un seul ticker — on ne
    # peut donc plus se fier à len(tickers_liste)==1 pour savoir si df a des
    # colonnes plates. On teste directement la structure de df.columns.
    colonnes_multi_niveaux = isinstance(df.columns, pd.MultiIndex)

    for ticker in tickers_liste:
        cle = ticker.strip().upper()
        try:
            if colonnes_multi_niveaux:
                sous_df = df[ticker]
            else:
                sous_df = df
            sous_df = sous_df.dropna(subset=["Close"])
            if sous_df.empty:
                resultats[cle]["erreur"] = "aucune donnée retournée"
                continue

            prix_actuel = float(sous_df["Close"].iloc[-1])
            sma50 = float(sous_df["Close"].tail(50).mean()) if len(sous_df) >= 50 else None
            sma200 = float(sous_df["Close"].tail(200).mean()) if len(sous_df) >= 200 else None
            volume_actuel = float(sous_df["Volume"].iloc[-1]) if "Volume" in sous_df else None
            volume_moyen = float(sous_df["Volume"].tail(20).mean()) if "Volume" in sous_df and len(sous_df) >= 20 else None

            au_dessus_sma200 = (prix_actuel > sma200) if sma200 else None
            sma50_au_dessus_sma200 = (sma50 > sma200) if (sma50 and sma200) else None
            ratio_volume = (volume_actuel / volume_moyen) if volume_actuel and volume_moyen else None

            resultats[cle] = {
                "prix_actuel": prix_actuel,
                "devise": None,  # yf.download ne fournit pas la devise ; à compléter via fondamentaux si besoin
                "sma50": sma50,
                "sma200": sma200,
                "au_dessus_sma200": au_dessus_sma200,
                "sma50_au_dessus_sma200": sma50_au_dessus_sma200,
                "volume_actuel": volume_actuel,
                "volume_moyen": volume_moyen,
                "ratio_volume": ratio_volume,
                "erreur": None,
            }
        except Exception as e:
            resultats[cle]["erreur"] = str(e)

    return resultats
    """Récupère le prix actuel + données techniques légères via fast_info :
    SMA50/SMA200 et volumes, calculés par Yahoo lui-même (pas de calcul
    supplémentaire côté serveur, pas d'appel réseau additionnel — tout
    vient du même quote léger). Pensé pour un rafraîchissement fréquent."""
    if yf is None:
        return {"prix_actuel": None, "devise": None, "erreur": "yfinance non installé"}
    try:
        ticker_obj = _nouveau_ticker(ticker_symbol)
        fi = ticker_obj.fast_info

        def _get(cles):
            for cle in cles:
                try:
                    valeur = fi[cle]
                    if valeur is not None:
                        return valeur
                except (KeyError, TypeError):
                    continue
            return None

        prix = _get(["last_price", "lastPrice", "regular_market_price"])
        devise = _get(["currency"])
        sma50 = _get(["fifty_day_average", "fiftyDayAverage"])
        sma200 = _get(["two_hundred_day_average", "twoHundredDayAverage"])
        volume_actuel = _get(["last_volume", "lastVolume"])
        volume_moyen_3m = _get(["three_month_average_volume", "threeMonthAverageVolume"])
        volume_moyen_10j = _get(["ten_day_average_volume", "tenDayAverageVolume"])

        au_dessus_sma200 = None
        if prix is not None and sma200:
            au_dessus_sma200 = prix > sma200

        ratio_volume = None
        volume_reference = volume_moyen_3m or volume_moyen_10j
        if volume_actuel is not None and volume_reference:
            ratio_volume = volume_actuel / volume_reference

        return {
            "prix_actuel": prix,
            "devise": devise,
            "sma50": sma50,
            "sma200": sma200,
            "au_dessus_sma200": au_dessus_sma200,
            "volume_actuel": volume_actuel,
            "volume_moyen": volume_reference,
            "ratio_volume": ratio_volume,
            "erreur": None,
        }
    except Exception as e:
        return {"prix_actuel": None, "devise": None, "erreur": str(e)}


# ----------------------------------------------------------------------
# Utilitaires de formatage (repris de bilan_scanner.py)
# ----------------------------------------------------------------------

def fmt_money(val):
    if val is None:
        return "N/A"
    try:
        val = float(val)
    except (TypeError, ValueError):
        return "N/A"
    sign = "-" if val < 0 else ""
    val = abs(val)
    if val >= 1e9:
        return f"{sign}{val/1e9:.2f}Md"
    if val >= 1e6:
        return f"{sign}{val/1e6:.1f}M"
    if val >= 1e3:
        return f"{sign}{val/1e3:.1f}K"
    return f"{sign}{val:.0f}"


def fmt_pct(val):
    if val is None:
        return "N/A"
    try:
        return f"{float(val)*100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def safe_get(series_or_df, key, col_idx=0):
    try:
        if key in series_or_df.index:
            row = series_or_df.loc[key]
            return row.iloc[col_idx]
    except Exception:
        pass
    return None


def safe_get_series(df, key):
    try:
        if key in df.index:
            return df.loc[key]
    except Exception:
        pass
    return None


def calcule_note_sur_10(criteres):
    if not criteres:
        return None, 0
    somme_points = sum(c[0] for c in criteres)
    somme_min = sum(c[1] for c in criteres)
    somme_max = sum(c[2] for c in criteres)
    if somme_max == somme_min:
        return 5.0, len(criteres)
    note = (somme_points - somme_min) / (somme_max - somme_min) * 10
    note = max(0.0, min(10.0, note))
    return round(note, 1), len(criteres)


def verdict_label(note):
    # Pas d'emoji : la police par défaut de Kivy sur Android (compilée via
    # Buildozer) n'a pas ces glyphes et affiche des carrés vides. On préfixe
    # avec un code texte simple à la place ; l'app peut le mapper à une
    # couleur côté affichage sans dépendre du rendu du glyphe.
    if note is None:
        return "N/A DONNEES INSUFFISANTES"
    if note >= 6.5:
        return "OK POSITIF"
    if note >= 4:
        return "MOYEN MITIGE"
    return "KO NEGATIF"


def verdict_synthese(note_sante, note_div):
    notes_disponibles = [n for n in (note_sante, note_div) if n is not None]
    if not notes_disponibles:
        return None, "N/A Données insuffisantes"
    moyenne = sum(notes_disponibles) / len(notes_disponibles)
    if moyenne >= 6.5:
        return moyenne, "OK Bon profil global"
    if moyenne >= 4:
        return moyenne, "MOYEN Correct, à surveiller"
    return moyenne, "KO Prudence recommandée"


# ----------------------------------------------------------------------
# Analyse santé financière (mêmes critères/poids que bilan_scanner.py v10)
# ----------------------------------------------------------------------

def analyse_sante_financiere(ticker_obj):
    """Retourne (note_sur_10, fcf, liste_de_points_cles)."""
    try:
        income = ticker_obj.income_stmt
        balance = ticker_obj.balance_sheet
        cashflow = ticker_obj.cashflow
    except Exception:
        return None, None, []

    if income is None or income.empty:
        return None, None, []

    criteres = []
    notes = []

    # Critère 1 : tendance CA sur 3-4 exercices
    revenue = safe_get_series(income, "Total Revenue")
    if revenue is not None and len(revenue) >= 2:
        n = min(len(revenue), 4)
        taux_croissance = []
        for i in range(n - 1):
            ca_recent_i = revenue.iloc[i]
            ca_precedent_i = revenue.iloc[i + 1]
            if ca_precedent_i and ca_precedent_i != 0:
                taux_croissance.append((ca_recent_i - ca_precedent_i) / abs(ca_precedent_i))
        if taux_croissance:
            croissance_moyenne = sum(taux_croissance) / len(taux_croissance)
            if croissance_moyenne > 0.05:
                criteres.append((2, -2, 2))
                notes.append(f"tendance CA solide ({fmt_pct(croissance_moyenne)}/an en moyenne)")
            elif croissance_moyenne < -0.05:
                criteres.append((-2, -2, 2))
                notes.append(f"tendance CA en baisse ({fmt_pct(croissance_moyenne)}/an en moyenne)")
            else:
                criteres.append((0, -2, 2))
                notes.append("tendance CA stable")

    # Critère 2 : résultat net et marge nette
    net_income = safe_get_series(income, "Net Income")
    if net_income is not None and revenue is not None and len(net_income) >= 1:
        ni_recent = net_income.iloc[0]
        marge_nette = ni_recent / revenue.iloc[0] if revenue.iloc[0] else None
        if ni_recent is not None:
            if ni_recent < 0:
                criteres.append((-3, -3, 2))
                notes.append("résultat net négatif")
            elif marge_nette and marge_nette > 0.15:
                criteres.append((2, -3, 2))
                notes.append(f"marge nette élevée ({fmt_pct(marge_nette)})")
            elif marge_nette and marge_nette > 0.08:
                criteres.append((1, -3, 2))
                notes.append(f"marge nette correcte ({fmt_pct(marge_nette)})")
            else:
                criteres.append((0, -3, 2))
                notes.append("marge nette faible")

    ebitda = safe_get(income, "EBITDA")

    # Critère 3 : endettement, dette nette / EBITDA
    total_debt = safe_get(balance, "Total Debt")
    cash = safe_get(balance, "Cash And Cash Equivalents")
    if total_debt is not None and cash is not None and ebitda:
        dette_nette = total_debt - cash
        ratio_dette_ebitda = dette_nette / ebitda if ebitda else None
        if ratio_dette_ebitda is not None:
            if ratio_dette_ebitda > 4:
                criteres.append((-3, -3, 2))
                notes.append(f"endettement très élevé ({ratio_dette_ebitda:.1f}x EBITDA)")
            elif ratio_dette_ebitda > 2.5:
                criteres.append((-1, -3, 2))
                notes.append(f"endettement élevé ({ratio_dette_ebitda:.1f}x EBITDA)")
            elif ratio_dette_ebitda < 1:
                criteres.append((2, -3, 2))
                notes.append("endettement très maîtrisé")
            else:
                criteres.append((1, -3, 2))
                notes.append("endettement raisonnable")

    # Critère 4 : Free Cash Flow
    ocf = safe_get(cashflow, "Operating Cash Flow")
    capex = safe_get(cashflow, "Capital Expenditure")
    fcf = None
    if ocf is not None and capex is not None:
        fcf = ocf + capex
        if fcf < 0:
            criteres.append((-2, -2, 2))
            notes.append("Free Cash Flow négatif")
        else:
            criteres.append((2, -2, 2))
            notes.append("Free Cash Flow positif")

    # Critère 5 : capitaux propres
    equity = safe_get(balance, "Stockholders Equity")
    if equity is not None:
        if equity < 0:
            criteres.append((-3, -3, 1))
            notes.append("capitaux propres négatifs (signal fort de risque)")
        else:
            criteres.append((1, -3, 1))
            notes.append("capitaux propres positifs")

    note, _ = calcule_note_sur_10(criteres)
    return note, fcf, notes


# ----------------------------------------------------------------------
# Analyse fiabilité dividende (mêmes critères/poids que bilan_scanner.py v10)
# ----------------------------------------------------------------------

def obtenir_calendrier_dividendes(ticker_obj, jours_passes=400):
    """Retourne les versements de dividende récents (jours_passes derniers
    jours, déjà effectivement versés selon Yahoo) + le prochain versement
    prévu s'il est connu et dans le futur (ex-dividend date + montant
    annoncé). Format : liste de dicts {"date": "YYYY-MM-DD", "montant":
    float, "prevu": bool}, triée par date croissante.

    Sert au suivi "calendrier de versements" de l'onglet Dividendes de
    l'app — distinct de analyse_dividende() qui, elle, calcule une note de
    fiabilité sur des critères structurels (régularité, payout ratio...).
    """
    evenements = []

    try:
        historique = ticker_obj.dividends
        if historique is not None and not historique.empty:
            limite = datetime.now() - timedelta(days=jours_passes)
            for date_idx, montant in historique.items():
                d = date_idx.to_pydatetime().replace(tzinfo=None)
                if d >= limite:
                    evenements.append({
                        "date": d.strftime("%Y-%m-%d"),
                        "montant": round(float(montant), 6),
                        "prevu": False,
                    })
    except Exception:
        pass

    try:
        info = ticker_obj.info
        ex_div_ts = info.get("exDividendDate")
        montant_prevu = info.get("dividendRate")
        if ex_div_ts and montant_prevu:
            date_prevue_dt = datetime.fromtimestamp(ex_div_ts)
            date_prevue = date_prevue_dt.strftime("%Y-%m-%d")
            deja_present = any(e["date"] == date_prevue for e in evenements)
            if not deja_present and date_prevue_dt > datetime.now():
                evenements.append({
                    "date": date_prevue,
                    "montant": round(float(montant_prevu), 6),
                    "prevu": True,
                })
    except Exception:
        pass

    evenements.sort(key=lambda e: e["date"])
    return evenements


def analyse_dividende(ticker_obj, fcf=None):
    """Retourne (note_sur_10 ou None si pas de dividende, liste_de_points_cles)."""
    info = {}
    try:
        info = ticker_obj.info
    except Exception:
        pass

    dividends = None
    try:
        dividends = ticker_obj.dividends
    except Exception:
        pass

    if dividends is None or dividends.empty:
        return None, ["Aucun historique de dividende"]

    criteres = []
    notes = []

    dividends_by_year = dividends.groupby(dividends.index.year).sum()
    annees = sorted(dividends_by_year.index)
    nb_annees = len(annees)

    annee_courante = datetime.now().year
    annees_completes = [a for a in annees if a < annee_courante]
    coupures = 0
    for i in range(1, len(annees_completes)):
        if annees_completes[i] - annees_completes[i - 1] > 1:
            coupures += 1
    if coupures == 0 and nb_annees >= 10:
        criteres.append((2, -2, 2))
        notes.append(f"versement régulier depuis {nb_annees} ans, sans interruption détectée")
    elif coupures == 0 and nb_annees >= 5:
        criteres.append((1, -2, 2))
        notes.append(f"versement régulier depuis {nb_annees} ans, historique encore limité")
    elif coupures > 0:
        criteres.append((-2, -2, 2))
        notes.append(f"{coupures} interruption(s) détectée(s) dans l'historique")
    else:
        criteres.append((0, -2, 2))
        notes.append("historique trop court pour juger de la régularité")

    streak_sans_baisse = 0
    for i in range(len(annees_completes) - 1, 0, -1):
        montant_actuel = dividends_by_year[annees_completes[i]]
        montant_precedent = dividends_by_year[annees_completes[i - 1]]
        if montant_actuel >= montant_precedent:
            streak_sans_baisse += 1
        else:
            break
    if annees_completes:
        if streak_sans_baisse >= 10:
            criteres.append((2, -1, 2))
            notes.append(f"profil aristocrate ({streak_sans_baisse} ans sans baisse)")
        elif streak_sans_baisse >= 5:
            criteres.append((1, -1, 2))
            notes.append(f"bon historique de progression ({streak_sans_baisse} ans sans baisse)")
        elif streak_sans_baisse == 0 and len(annees_completes) >= 2:
            criteres.append((-1, -1, 2))
            notes.append("dividende baissé lors du dernier exercice complet")
        else:
            criteres.append((0, -1, 2))
            notes.append(f"historique de progression encore court ({streak_sans_baisse} ans)")

    if len(annees_completes) >= 4:
        div_recent = dividends_by_year[annees_completes[-1]]
        div_ancien = dividends_by_year[annees_completes[-4]]
        if div_ancien and div_ancien > 0:
            cagr = (div_recent / div_ancien) ** (1 / 3) - 1
            if cagr > 0.03:
                criteres.append((1, -1, 1))
                notes.append(f"dividende en croissance ({fmt_pct(cagr)}/an)")
            elif cagr < -0.03:
                criteres.append((-1, -1, 1))
                notes.append(f"dividende en baisse ({fmt_pct(cagr)}/an)")
            else:
                criteres.append((0, -1, 1))
                notes.append("dividende stable")

    payout = info.get("payoutRatio")
    if payout is not None:
        if payout > 0.90:
            criteres.append((-2, -2, 1))
            notes.append(f"payout ratio très élevé ({fmt_pct(payout)}) — risque de coupe")
        elif payout > 0.70:
            criteres.append((-1, -2, 1))
            notes.append(f"payout ratio élevé ({fmt_pct(payout)})")
        elif payout < 0.60:
            criteres.append((1, -2, 1))
            notes.append(f"payout ratio soutenable ({fmt_pct(payout)})")
        else:
            criteres.append((0, -2, 1))
            notes.append(f"payout ratio moyen ({fmt_pct(payout)})")

    div_rate = info.get("trailingAnnualDividendRate")
    shares_out = info.get("sharesOutstanding")
    if div_rate and shares_out and fcf is not None:
        div_total_verse = div_rate * shares_out
        if fcf > 0:
            couverture_fcf = fcf / div_total_verse if div_total_verse else None
            if couverture_fcf is not None:
                if couverture_fcf < 1:
                    criteres.append((-2, -2, 1))
                    notes.append("dividende non couvert par le FCF — signal d'alerte fort")
                elif couverture_fcf > 1.5:
                    criteres.append((1, -2, 1))
                    notes.append("dividende bien couvert par le FCF")
                else:
                    criteres.append((0, -2, 1))
                    notes.append("dividende couvert de justesse par le FCF")
        else:
            criteres.append((-2, -2, 1))
            notes.append("FCF négatif alors qu'un dividende est versé — signal d'alerte fort")

    yield_actuel = info.get("dividendYield")
    yield_5y = info.get("fiveYearAvgDividendYield")
    if yield_actuel is not None:
        ya = yield_actuel if yield_actuel < 1 else yield_actuel / 100
        if yield_5y is not None:
            y5 = yield_5y if yield_5y < 1 else yield_5y / 100
            if y5 > 0 and ya > y5 * 1.5:
                criteres.append((-1, -1, 0))
                notes.append("rendement anormalement élevé vs historique — vérifier si le cours a chuté")
            else:
                criteres.append((0, -1, 0))
                notes.append("rendement cohérent avec la moyenne historique")

    note, _ = calcule_note_sur_10(criteres)
    return note, notes


# ----------------------------------------------------------------------
# Point d'entrée unique utilisé par l'app : une position -> données complètes
# ----------------------------------------------------------------------

def analyser_position(ticker_symbol, quantite=None, pru=None):
    """
    Analyse complète d'un ticker pour l'app de suivi de positions.
    Retourne un dict prêt à afficher :
      ticker, nom, prix_actuel, valeur_position, pv_mv_eur, pv_mv_pct,
      prochain_dividende_date, prochain_dividende_montant, rendement_pct,
      note_sante, note_div, notes_sante, notes_div, verdict, erreur
    """
    resultat = {
        "ticker": ticker_symbol,
        "nom": ticker_symbol,
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
        "verdict": "N/A Données insuffisantes",
        "erreur": None,
        "avis_analystes": None,
    }

    if yf is None:
        resultat["erreur"] = "yfinance non installé"
        return resultat

    try:
        ticker_obj = _nouveau_ticker(ticker_symbol)
        info = {}
        try:
            info = ticker_obj.info or {}
        except Exception:
            info = {}

        resultat["nom"] = info.get("shortName") or info.get("longName") or ticker_symbol
        resultat["devise"] = info.get("currency")

        # --- Avis analystes (aucun appel réseau supplémentaire, réutilise .info) ---
        _LIBELLES_RECO = {
            "strong_buy": "Achat fort", "buy": "Achat", "hold": "Conserver",
            "sell": "Vente", "strong_sell": "Vente forte",
            "underperform": "Sous-performance", "outperform": "Sur-performance",
        }
        cle_reco = info.get("recommendationKey")
        prix_cible_moyen = info.get("targetMeanPrice")
        prix_ref = info.get("currentPrice") or info.get("regularMarketPrice")
        resultat["avis_analystes"] = {
            "consensus": _LIBELLES_RECO.get(cle_reco, cle_reco),
            "nb_analystes": info.get("numberOfAnalystOpinions"),
            "prix_cible_moyen": prix_cible_moyen,
            "prix_cible_haut": info.get("targetHighPrice"),
            "prix_cible_bas": info.get("targetLowPrice"),
            "potentiel_pct": ((prix_cible_moyen - prix_ref) / prix_ref * 100)
                             if (prix_cible_moyen and prix_ref) else None,
        } if (cle_reco or prix_cible_moyen) else None

        prix = info.get("currentPrice") or info.get("regularMarketPrice")
        resultat["prix_actuel"] = prix

        if prix is not None and quantite is not None:
            valeur = prix * quantite
            resultat["valeur_position"] = valeur
            if pru is not None:
                cout_total = pru * quantite
                resultat["pv_mv_eur"] = valeur - cout_total
                resultat["pv_mv_pct"] = ((prix - pru) / pru * 100) if pru else None

        # Prochain dividende : ex-dividend date + montant estimé (dernier versement)
        ex_div_ts = info.get("exDividendDate")
        if ex_div_ts:
            try:
                resultat["prochain_dividende_date"] = datetime.fromtimestamp(ex_div_ts).strftime("%d/%m/%Y")
            except Exception:
                pass
        div_rate = info.get("dividendRate")
        resultat["prochain_dividende_montant"] = div_rate
        # Calcul direct dividende/prix plutôt que de se fier au champ
        # dividendYield de yfinance : son format (fraction 0.0033 vs déjà
        # en pourcentage 0.33) a changé selon les versions et n'est plus
        # fiable. Le calcul manuel est indépendant de ce format.
        if div_rate is not None and prix:
            resultat["rendement_pct"] = div_rate / prix * 100
        else:
            yld = info.get("dividendYield")
            if yld is not None:
                resultat["rendement_pct"] = yld * 100 if yld < 1 else yld

        note_sante, fcf, notes_sante = analyse_sante_financiere(ticker_obj)
        note_div, notes_div = analyse_dividende(ticker_obj, fcf=fcf)

        resultat["note_sante"] = note_sante
        resultat["note_div"] = note_div
        resultat["notes_sante"] = notes_sante
        resultat["notes_div"] = notes_div

        _, verdict_txt = verdict_synthese(note_sante, note_div)
        resultat["verdict"] = verdict_txt

    except Exception as e:
        resultat["erreur"] = str(e)

    return resultat


# ----------------------------------------------------------------------
# Actualités + sentiment (lexique simple, pas de dépendance ML lourde)
# ----------------------------------------------------------------------

_MOTS_POSITIFS = {
    "surge", "soar", "beat", "beats", "growth", "record", "profit", "gain", "gains",
    "upgrade", "outperform", "strong", "rally", "boost", "positive", "rise", "rises",
    "jump", "jumps", "bullish", "success", "win", "wins", "expand", "expansion",
    "hausse", "croissance", "succès", "optimiste",
}
_MOTS_NEGATIFS = {
    "plunge", "fall", "falls", "drop", "drops", "loss", "losses", "downgrade",
    "underperform", "weak", "decline", "cut", "cuts", "warning", "negative",
    "slump", "bearish", "miss", "misses", "lawsuit", "investigation", "scandal",
    "baisse", "chute", "perte", "avertissement", "enquête", "recul",
}

# Mots qui inversent le sens d'un mot de sentiment trouvé juste après eux
# (ex: "avoids lawsuit", "pas de croissance") — sans ça, un titre positif
# contenant un mot-clé négatif niké (ou l'inverse) était mal classé.
_MOTS_NEGATION = {
    "not", "no", "never", "without", "avoids", "avoid",
    "pas", "sans", "aucun", "aucune", "ni", "évite", "évitent",
}
_PORTEE_NEGATION = 3  # nombre de mots avant le mot-clé où une négation compte encore


def _score_sentiment(texte):
    """Score de sentiment simple, basé sur un lexique anglais/français avec
    prise en compte de la négation proche (voir _MOTS_NEGATION). Retourne
    (label, force) où force = |nb_mots_positifs - nb_mots_negatifs| après
    inversion des mots niés. Une force de 1 (ex: 1 mot positif, 0 négatif)
    est un signal faible ; on ne le considère "marqué" qu'à partir de 2
    pour limiter les faux positifs sur un simple titre ambigu. Indication
    rapide, pas une vraie analyse NLP — évite d'ajouter une dépendance ML
    lourde."""
    if not texte:
        return "neutre", 0
    mots = texte.lower().replace(",", " ").replace(".", " ").replace("'", " ").split()

    pos = 0
    neg = 0
    for i, mot in enumerate(mots):
        fenetre_avant = mots[max(0, i - _PORTEE_NEGATION):i]
        nie = any(w in _MOTS_NEGATION for w in fenetre_avant)
        if mot in _MOTS_POSITIFS:
            if nie:
                neg += 1
            else:
                pos += 1
        elif mot in _MOTS_NEGATIFS:
            if nie:
                pos += 1
            else:
                neg += 1

    force = abs(pos - neg)
    if pos > neg:
        return "positif", force
    if neg > pos:
        return "negatif", force
    return "neutre", 0


def obtenir_actualites(ticker_symbol, limit=6, nom_entreprise=None):
    """Retourne une liste de dicts {titre, editeur, date, epoch, lien,
    sentiment, source} pour les dernières actualités d'un ticker.

    Uniquement Google News RSS (recherché par nom d'entreprise). Le flux
    yfinance (ticker.news) a été retiré : couverture faible, structure
    instable (change sans prévenir), et il sollicitait en plus le même
    backend Yahoo déjà mis à rude épreuve pour prix/fondamentaux — sans
    apporter de valeur ajoutée par rapport à Google News, qui agrège
    beaucoup plus de sources et ne dépend pas de Yahoo."""
    resultats = []
    mots_vus = []  # une entrée par titre déjà retenu : set des mots significatifs

    def _mots_significatifs(titre):
        return {m for m in titre.lower().split() if len(m) > 3}

    def _similarite_jaccard(a, b):
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _ajouter(item):
        titre = (item.get("titre") or "").strip()
        if not titre:
            return
        mots_titre = _mots_significatifs(titre)
        # Seuil 0.35 : calibré empiriquement sur des reformulations réelles
        # (ex: "Apple stock surges after strong earnings beat" vs "Apple
        # shares surge following strong earnings beat report" ≈ 0.36) —
        # au-delà, c'est quasi toujours la même dépêche reprise par
        # plusieurs médias (au lieu d'une simple comparaison de préfixe,
        # qui ratait les reformulations et laissait passer des doublons).
        for autre in mots_vus:
            if _similarite_jaccard(mots_titre, autre) >= 0.35:
                return
        mots_vus.append(mots_titre)
        resultats.append(item)

    # Dépôts réglementaires officiels SEC (US uniquement, silencieux sinon)
    for item in _obtenir_actualites_sec_edgar(ticker_symbol, limit=3):
        _ajouter(item)

    # Communiqués réglementés AMF — DÉSACTIVÉ pour le moment : l'ancien
    # dataset "flux-amf-new-prod" (info-financiere.gouv.fr) est un système
    # discontinué qui ne renvoie que des archives jusqu'à ~2013, malgré son
    # nom. L'AMF a migré vers une nouvelle plateforme (BDIF,
    # bdif.amf-france.org) avec un flux RSS par société, mais l'URL exacte
    # n'a pas encore été vérifiée (page en JavaScript, pas de contenu
    # visible sans l'ouvrir dans un vrai navigateur). Voir AUTO_RELANCE.md
    # ou demander à Claude de finir l'intégration une fois l'URL du flux
    # RSS BDIF récupérée manuellement.
    # if ticker_symbol.strip().upper().endswith(".PA"):
    #     for item in _obtenir_actualites_amf(nom_entreprise, limit=3):
    #         _ajouter(item)

    terme_recherche = nom_entreprise or ticker_symbol
    for item in _obtenir_actualites_google_news(terme_recherche, limit=limit):
        _ajouter(item)

    # Tri par date décroissante (les items sans date connue vont en dernier)
    resultats.sort(key=lambda x: x.get("epoch") or 0, reverse=True)
    return resultats[:limit]


def _obtenir_actualites_google_news(terme_recherche, limit=6):
    """Interroge le flux RSS de recherche Google News, filtré par nom
    d'entreprise. Fiable et universel : pas besoin de connaître un code
    interne par titre comme sur Boursorama/Zonebourse."""
    try:
        import feedparser
    except ImportError:
        return []

    try:
        import urllib.parse
        requete = urllib.parse.quote(terme_recherche)
        url = f"https://news.google.com/rss/search?q={requete}&hl=fr&gl=FR&ceid=FR:fr"
        flux = feedparser.parse(url)
    except Exception:
        return []

    resultats = []
    for entree in flux.entries[:limit]:
        titre = getattr(entree, "title", "") or ""
        lien = getattr(entree, "link", "") or ""
        editeur = ""
        if hasattr(entree, "source") and entree.source:
            editeur = getattr(entree.source, "title", "") or ""
        epoch_pub = None
        date_txt = ""
        if hasattr(entree, "published_parsed") and entree.published_parsed:
            try:
                dt_obj = datetime(*entree.published_parsed[:6])
                epoch_pub = dt_obj.timestamp()
                date_txt = dt_obj.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        if not titre:
            continue

        sentiment_label, force_sentiment = _score_sentiment(titre)
        resultats.append({
            "titre": titre,
            "editeur": editeur or "Google News",
            "date": date_txt,
            "epoch": epoch_pub,
            "lien": lien,
            "sentiment": sentiment_label,
            "force_sentiment": force_sentiment,
            "source": "Google News",
        })

    return resultats


# ----------------------------------------------------------------------
# AMF / info-financière.fr — communiqués réglementés officiels des
# sociétés cotées françaises (résultats, OPA, franchissements de seuil...).
# Champs confirmés par test réel sur l'API OpenDataSoft (dataset
# "flux-amf-new-prod"), pas de supposition.
# ----------------------------------------------------------------------
def _obtenir_actualites_amf(nom_entreprise, limit=4):
    """Communiqués réglementés AMF pour une société française, recherchés
    par nom (le champ ticker du dataset AMF utilise d'anciens mnémoniques
    Euronext qui ne correspondent pas toujours au ticker Yahoo — la
    recherche par nom est plus fiable)."""
    if not nom_entreprise:
        return []
    try:
        import requests
        params = {
            "dataset": "flux-amf-new-prod",
            "q": nom_entreprise,
            "rows": limit,
            "sort": "-uin_dat_amf",
        }
        resp = requests.get(
            "https://dilaamf.opendatasoft.com/api/records/1.0/search/",
            params=params, timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    resultats = []
    for record in data.get("records", []):
        champs = record.get("fields", {})
        titre = champs.get("informationdeposee_inf_tit_inf", "")
        if not titre:
            continue

        date_brute = champs.get("uin_dat_amf", "")
        epoch_pub = None
        date_txt = ""
        if date_brute:
            try:
                dt_obj = datetime.fromisoformat(date_brute.replace("Z", "+00:00"))
                epoch_pub = dt_obj.timestamp()
                date_txt = dt_obj.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        sentiment_label, force_sentiment = _score_sentiment(titre)
        resultats.append({
            "titre": titre,
            "editeur": "AMF",
            "date": date_txt,
            "epoch": epoch_pub,
            "lien": champs.get("url_de_recuperation", ""),
            "sentiment": sentiment_label,
            "force_sentiment": force_sentiment,
            "source": "AMF (officiel)",
        })

    return resultats
# souvent LE déclencheur direct d'un mouvement de cours). Format stable et
# documenté par la SEC, contrairement au scraping fragile d'autres sources.
# ----------------------------------------------------------------------
_CACHE_CIK = {}  # ticker -> CIK (résolu une fois, mémorisé pour la session)


def _resoudre_cik(ticker_symbol):
    """Résout un ticker US vers son CIK SEC via le fichier officiel
    company_tickers.json (mis en cache mémoire après le premier appel)."""
    global _CACHE_CIK
    ticker_norm = ticker_symbol.strip().upper()

    if not _CACHE_CIK:
        try:
            import requests
            headers = {"User-Agent": "SuiviBourse perso contact@example.com"}
            resp = requests.get("https://www.sec.gov/files/company_tickers.json",
                                 headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for entree in data.values():
                _CACHE_CIK[entree["ticker"].upper()] = str(entree["cik_str"]).zfill(10)
        except Exception:
            return None

    return _CACHE_CIK.get(ticker_norm)


def _obtenir_actualites_sec_edgar(ticker_symbol, limit=6):
    """Dépôts réglementaires récents (formulaire 8-K en priorité) pour un
    ticker US, via le flux ATOM officiel de la SEC. Ne renvoie rien pour
    les tickers non-US (ex: suffixe .PA, .AS) — inutile d'essayer."""
    if "." in ticker_symbol:  # heuristique simple : tickers non-US ont un suffixe (.PA, .AS, .DE...)
        return []

    cik = _resoudre_cik(ticker_symbol)
    if not cik:
        return []

    try:
        import requests
        headers = {"User-Agent": "SuiviBourse perso contact@example.com"}
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            f"&type=8-K&dateb=&owner=include&count={limit}&output=atom"
        )
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
    except Exception:
        return []

    try:
        import feedparser
        flux = feedparser.parse(resp.text)
    except ImportError:
        return []

    resultats = []
    for entree in flux.entries[:limit]:
        titre = getattr(entree, "title", "") or ""
        lien = getattr(entree, "link", "") or ""
        epoch_pub = None
        date_txt = ""
        if hasattr(entree, "updated_parsed") and entree.updated_parsed:
            try:
                dt_obj = datetime(*entree.updated_parsed[:6])
                epoch_pub = dt_obj.timestamp()
                date_txt = dt_obj.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        if not titre:
            continue

        sentiment_label, force_sentiment = _score_sentiment(titre)
        resultats.append({
            "titre": f"[Dépôt SEC] {titre}",
            "editeur": "SEC EDGAR",
            "date": date_txt,
            "epoch": epoch_pub,
            "lien": lien,
            "sentiment": sentiment_label,
            "force_sentiment": force_sentiment,
            "source": "SEC EDGAR (officiel)",
        })

    return resultats
    """Interroge le flux RSS de recherche Google News, filtré par nom
    d'entreprise. Fiable et universel : pas besoin de connaître un code
    interne par titre comme sur Boursorama/Zonebourse."""
    try:
        import feedparser
    except ImportError:
        return []

    try:
        import urllib.parse
        requete = urllib.parse.quote(terme_recherche)
        url = f"https://news.google.com/rss/search?q={requete}&hl=fr&gl=FR&ceid=FR:fr"
        flux = feedparser.parse(url)
    except Exception:
        return []

    resultats = []
    for entree in flux.entries[:limit]:
        titre = getattr(entree, "title", "") or ""
        lien = getattr(entree, "link", "") or ""
        editeur = ""
        if hasattr(entree, "source") and entree.source:
            editeur = getattr(entree.source, "title", "") or ""
        epoch_pub = None
        date_txt = ""
        if hasattr(entree, "published_parsed") and entree.published_parsed:
            try:
                dt_obj = datetime(*entree.published_parsed[:6])
                epoch_pub = dt_obj.timestamp()
                date_txt = dt_obj.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        if not titre:
            continue

        sentiment_label, force_sentiment = _score_sentiment(titre)
        resultats.append({
            "titre": titre,
            "editeur": editeur or "Google News",
            "date": date_txt,
            "epoch": epoch_pub,
            "lien": lien,
            "sentiment": sentiment_label,
            "force_sentiment": force_sentiment,
            "source": "Google News",
        })

    return resultats


if __name__ == "__main__":
    # Debug rapide en ligne de commande : python scoring.py MC.PA 10 650
    if len(sys.argv) < 2:
        print("Usage: python scoring.py TICKER [quantite] [pru]")
        sys.exit(1)
    tk = sys.argv[1]
    qte = float(sys.argv[2]) if len(sys.argv) > 2 else None
    pru_val = float(sys.argv[3]) if len(sys.argv) > 3 else None
    r = analyser_position(tk, qte, pru_val)
    for k, v in r.items():
        print(f"{k}: {v}")
