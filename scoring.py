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

import sys
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    yf = None


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
    if note is None:
        return "⚪ DONNEES INSUFFISANTES"
    if note >= 6.5:
        return "🟢 POSITIF"
    if note >= 4:
        return "🟠 MITIGE"
    return "🔴 NEGATIF"


def verdict_synthese(note_sante, note_div):
    notes_disponibles = [n for n in (note_sante, note_div) if n is not None]
    if not notes_disponibles:
        return None, "⚪ Données insuffisantes"
    moyenne = sum(notes_disponibles) / len(notes_disponibles)
    if moyenne >= 6.5:
        return moyenne, "🟢 Bon profil global"
    if moyenne >= 4:
        return moyenne, "🟠 Correct, à surveiller"
    return moyenne, "🔴 Prudence recommandée"


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
        "verdict": "⚪ Données insuffisantes",
        "erreur": None,
    }

    if yf is None:
        resultat["erreur"] = "yfinance non installé"
        return resultat

    try:
        ticker_obj = yf.Ticker(ticker_symbol)
        info = {}
        try:
            info = ticker_obj.info or {}
        except Exception:
            info = {}

        resultat["nom"] = info.get("shortName") or info.get("longName") or ticker_symbol
        resultat["devise"] = info.get("currency")

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
        resultat["prochain_dividende_montant"] = info.get("dividendRate")
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
