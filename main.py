#!/usr/bin/env python3
"""
main.py — Suivi Bourse (Kivy, client léger)

Version "client/serveur" de l'app : ne dépend que de kivy + requests
(plus de yfinance/pandas/numpy embarqués — trop lourds à cross-compiler
pour Android). La vraie logique de scoring tourne dans server.py, sur
ton PC/VPS/Termux, et cette app l'appelle en HTTP.

Écrans :
- Portefeuille : liste des positions, PV/MV, notes santé/dividende
- Ajouter : formulaire (ticker, quantité, PRU)
- Détail : prochain dividende, points clés
- Paramètres : adresse du serveur (http://IP:8765)

Lancement desktop (test) : python main.py
Packaging Android : voir buildozer.spec (léger, pas de recettes numpy/pandas)
"""

import threading
from datetime import datetime

from kivy.app import App
from kivy.clock import mainthread
from kivy.lang import Builder
from kivy.properties import StringProperty, ListProperty, BooleanProperty
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.label import Label

import storage
import api_client

KV = """
<PositionRow@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: dp(96)
    padding: dp(10), dp(6)
    spacing: dp(2)
    canvas.before:
        Color:
            rgba: 0.12, 0.12, 0.14, 1
        Rectangle:
            pos: self.pos
            size: self.size

    ticker: ""
    nom: ""
    pv_mv_txt: ""
    pv_mv_color: 1, 1, 1, 1
    sante_txt: "N/A"
    div_txt: "N/A"
    verdict: ""

    BoxLayout:
        size_hint_y: 0.55
        Label:
            text: root.nom
            bold: True
            halign: "left"
            valign: "middle"
            text_size: self.size
            shorten: True
        Label:
            text: root.pv_mv_txt
            color: root.pv_mv_color
            bold: True
            halign: "right"
            valign: "middle"
            text_size: self.size
            size_hint_x: 0.45

    BoxLayout:
        size_hint_y: 0.45
        Label:
            text: "Santé " + root.sante_txt + "/10"
            font_size: "12sp"
            color: 0.75, 0.75, 0.78, 1
            halign: "left"
            text_size: self.size
        Label:
            text: "Div " + root.div_txt + "/10"
            font_size: "12sp"
            color: 0.75, 0.75, 0.78, 1
            halign: "left"
            text_size: self.size
        Label:
            text: root.verdict
            font_size: "12sp"
            halign: "right"
            text_size: self.size

<PortfolioScreen>:
    name: "portfolio"
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(10), dp(6)
            canvas.before:
                Color:
                    rgba: 0.08, 0.08, 0.10, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            Label:
                text: "Mon portefeuille"
                bold: True
                font_size: "18sp"
                halign: "left"
                text_size: self.size
            Label:
                text: root.total_txt
                bold: True
                halign: "right"
                text_size: self.size
                color: root.total_color

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            padding: dp(6), dp(4)
            spacing: dp(6)
            Button:
                text: "+ Ajouter"
                on_release: root.manager.current = "add"
            Button:
                text: "Rafraîchir"
                disabled: root.refreshing
                on_release: root.rafraichir()
            Button:
                text: "Param."
                size_hint_x: 0.35
                on_release: root.manager.current = "settings"

        Label:
            text: root.erreur_globale
            color: 0.9, 0.6, 0.2, 1
            size_hint_y: None
            height: dp(28) if root.erreur_globale else 0
            font_size: "12sp"

        ScrollView:
            BoxLayout:
                id: liste_box
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height

<AddPositionScreen>:
    name: "add"
    BoxLayout:
        orientation: "vertical"
        padding: dp(20)
        spacing: dp(14)

        Label:
            text: "Nouvelle position"
            font_size: "20sp"
            bold: True
            size_hint_y: None
            height: dp(40)

        TextInput:
            id: ticker_input
            hint_text: "Ticker Yahoo Finance (ex: MC.PA, AAPL, TTE.PA)"
            multiline: False
            size_hint_y: None
            height: dp(48)

        TextInput:
            id: quantite_input
            hint_text: "Quantité"
            multiline: False
            input_filter: "float"
            size_hint_y: None
            height: dp(48)

        TextInput:
            id: pru_input
            hint_text: "Prix de revient unitaire (PRU)"
            multiline: False
            input_filter: "float"
            size_hint_y: None
            height: dp(48)

        Label:
            id: erreur_label
            text: ""
            color: 0.9, 0.3, 0.3, 1
            size_hint_y: None
            height: dp(30)

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(10)
            Button:
                text: "Annuler"
                on_release: root.annuler()
            Button:
                text: "Ajouter"
                on_release: root.ajouter()

        Widget:

<DetailScreen>:
    name: "detail"
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(10), dp(6)
            canvas.before:
                Color:
                    rgba: 0.08, 0.08, 0.10, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            Button:
                text: "< Retour"
                size_hint_x: 0.3
                on_release: root.manager.current = "portfolio"
            Label:
                text: root.nom
                bold: True
                font_size: "16sp"
                halign: "right"
                text_size: self.size

        ScrollView:
            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16)
                spacing: dp(10)

                Label:
                    text: root.resume_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"

                Label:
                    text: "[b]Points clés — Santé financière[/b]"
                    markup: True
                    size_hint_y: None
                    height: dp(30) if root.notes_sante else 0
                    text_size: self.width, None
                    halign: "left"

                Label:
                    text: root.notes_sante_txt
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"

                Label:
                    text: "[b]Points clés — Fiabilité dividende[/b]"
                    markup: True
                    size_hint_y: None
                    height: dp(30) if root.notes_div else 0
                    text_size: self.width, None
                    halign: "left"

                Label:
                    text: root.notes_div_txt
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"

                BoxLayout:
                    size_hint_y: None
                    height: dp(48)
                    spacing: dp(10)
                    Button:
                        text: "Supprimer la position"
                        on_release: root.supprimer()

<SettingsScreen>:
    name: "settings"
    BoxLayout:
        orientation: "vertical"
        padding: dp(20)
        spacing: dp(14)

        Label:
            text: "Paramètres"
            font_size: "20sp"
            bold: True
            size_hint_y: None
            height: dp(40)

        Label:
            text: "Adresse du serveur scoring (server.py)"
            size_hint_y: None
            height: dp(24)
            halign: "left"
            text_size: self.size

        TextInput:
            id: url_input
            hint_text: "http://192.168.1.X:8765"
            multiline: False
            size_hint_y: None
            height: dp(48)

        Label:
            id: statut_label
            text: root.statut_txt
            color: root.statut_color
            size_hint_y: None
            height: dp(30)

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            spacing: dp(10)
            Button:
                text: "Tester la connexion"
                on_release: root.tester()
            Button:
                text: "Enregistrer"
                on_release: root.enregistrer()

        Label:
            text:
                ("Cette app ne fait tourner aucun calcul localement : "
                "elle interroge un petit serveur (server.py) que tu lances "
                "sur ton PC, ton VPS, ou Termux, et qui lui fait le vrai "
                "travail (yfinance/pandas). Renseigne ici son adresse IP "
                "et son port (8765 par défaut).")
            size_hint_y: None
            height: self.texture_size[1]
            text_size: self.width, None
            halign: "left"
            font_size: "13sp"
            color: 0.7, 0.7, 0.72, 1

        Widget:

        Button:
            text: "< Retour au portefeuille"
            size_hint_y: None
            height: dp(48)
            on_release: root.manager.current = "portfolio"
"""


def couleur_pv(valeur):
    if valeur is None:
        return (1, 1, 1, 1)
    if valeur > 0:
        return (0.30, 0.80, 0.40, 1)
    if valeur < 0:
        return (0.90, 0.30, 0.30, 1)
    return (1, 1, 1, 1)


class PortfolioScreen(Screen):
    total_txt = StringProperty("")
    total_color = ListProperty([1, 1, 1, 1])
    refreshing = BooleanProperty(False)
    erreur_globale = StringProperty("")

    def on_pre_enter(self):
        self.rafraichir()

    def rafraichir(self):
        if self.refreshing:
            return
        self.refreshing = True
        self.erreur_globale = ""
        self.ids.liste_box.clear_widgets()
        self.ids.liste_box.add_widget(
            Label(text="Interrogation du serveur...", size_hint_y=None, height=60)
        )
        threading.Thread(target=self._charger_en_fond, daemon=True).start()

    def _charger_en_fond(self):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        positions = storage.charger_positions()
        resultats = []
        for pos in positions:
            r = api_client.analyser_position(server_url, pos["ticker"], pos["quantite"], pos["pru"])
            resultats.append(r)
        self._afficher_resultats(resultats, server_url)

    @mainthread
    def _afficher_resultats(self, resultats, server_url):
        self.ids.liste_box.clear_widgets()
        total_pv = 0.0
        total_connu = False
        erreurs_connexion = 0

        if not resultats:
            self.ids.liste_box.add_widget(
                Label(text="Aucune position. Appuie sur + Ajouter.",
                      size_hint_y=None, height=80)
            )

        for i, r in enumerate(resultats):
            from kivy.factory import Factory
            row = Factory.PositionRow()
            row.ticker = r["ticker"]
            row.nom = r.get("nom") or r["ticker"]
            if r.get("erreur"):
                if "injoignable" in r["erreur"] or "Timeout" in r["erreur"]:
                    erreurs_connexion += 1
                row.pv_mv_txt = "Erreur"
                row.pv_mv_color = (0.9, 0.6, 0.2, 1)
            elif r.get("pv_mv_eur") is not None:
                total_pv += r["pv_mv_eur"]
                total_connu = True
                signe = "+" if r["pv_mv_eur"] >= 0 else ""
                pct = r.get("pv_mv_pct")
                pct_txt = f" ({signe}{pct:.1f}%)" if pct is not None else ""
                row.pv_mv_txt = f"{signe}{r['pv_mv_eur']:.2f}{pct_txt}"
                row.pv_mv_color = couleur_pv(r["pv_mv_eur"])
            else:
                row.pv_mv_txt = "N/A"
            row.sante_txt = f"{r['note_sante']}" if r.get("note_sante") is not None else "N/A"
            row.div_txt = f"{r['note_div']}" if r.get("note_div") is not None else "N/A"
            row.verdict = r.get("verdict", "")

            index = i
            row.bind(on_touch_up=lambda inst, touch, idx=index, res=r:
                      self._ouvrir_detail(idx, res) if inst.collide_point(*touch.pos) else None)
            self.ids.liste_box.add_widget(row)

        if erreurs_connexion and erreurs_connexion == len(resultats) and resultats:
            self.erreur_globale = f"Serveur injoignable à {server_url} — vérifie Paramètres."
        else:
            self.erreur_globale = ""

        self.total_txt = f"PV/MV totale: {'+' if total_pv >= 0 else ''}{total_pv:.2f} €" if total_connu else ""
        self.total_color = couleur_pv(total_pv if total_connu else None)
        self.refreshing = False

    def _ouvrir_detail(self, index, resultat):
        detail = self.manager.get_screen("detail")
        detail.charger(index, resultat)
        self.manager.current = "detail"


class AddPositionScreen(Screen):
    def annuler(self):
        self.ids.ticker_input.text = ""
        self.ids.quantite_input.text = ""
        self.ids.pru_input.text = ""
        self.ids.erreur_label.text = ""
        self.manager.current = "portfolio"

    def ajouter(self):
        ticker = self.ids.ticker_input.text.strip()
        quantite = self.ids.quantite_input.text.strip()
        pru = self.ids.pru_input.text.strip()

        if not ticker or not quantite or not pru:
            self.ids.erreur_label.text = "Tous les champs sont obligatoires."
            return
        try:
            quantite_f = float(quantite)
            pru_f = float(pru)
            if quantite_f <= 0 or pru_f <= 0:
                raise ValueError
        except ValueError:
            self.ids.erreur_label.text = "Quantité et PRU doivent être des nombres positifs."
            return

        storage.ajouter_position(ticker, quantite_f, pru_f,
                                  date_achat=datetime.now().strftime("%Y-%m-%d"))
        self.ids.ticker_input.text = ""
        self.ids.quantite_input.text = ""
        self.ids.pru_input.text = ""
        self.ids.erreur_label.text = ""
        self.manager.current = "portfolio"


class DetailScreen(Screen):
    nom = StringProperty("")
    resume_txt = StringProperty("")
    notes_sante = ListProperty([])
    notes_div = ListProperty([])
    notes_sante_txt = StringProperty("")
    notes_div_txt = StringProperty("")
    _index = None

    def charger(self, index, resultat):
        self._index = index
        self.nom = resultat.get("nom") or resultat.get("ticker", "")
        r = resultat

        lignes = [f"[b]{r.get('nom')}[/b] ({r.get('ticker')})", ""]
        if r.get("erreur"):
            lignes.append(f"[color=e04c4c]Erreur: {r['erreur']}[/color]")
        else:
            prix = r.get("prix_actuel")
            devise = r.get("devise") or ""
            lignes.append(f"Prix actuel : {prix} {devise}" if prix is not None else "Prix actuel : N/A")
            if r.get("valeur_position") is not None:
                lignes.append(f"Valeur position : {r['valeur_position']:.2f} {devise}")
            if r.get("pv_mv_eur") is not None:
                signe = "+" if r["pv_mv_eur"] >= 0 else ""
                pct = r.get("pv_mv_pct")
                pct_txt = f" ({signe}{pct:.1f}%)" if pct is not None else ""
                couleur = "5ecc66" if r["pv_mv_eur"] >= 0 else "e04c4c"
                lignes.append(f"[color={couleur}]PV/MV : {signe}{r['pv_mv_eur']:.2f} {devise}{pct_txt}[/color]")
            lignes.append("")
            if r.get("prochain_dividende_date"):
                lignes.append(f"Prochain détachement (ex-div) : {r['prochain_dividende_date']}")
            if r.get("prochain_dividende_montant"):
                lignes.append(f"Dividende annuel estimé : {r['prochain_dividende_montant']} / action")
            if r.get("rendement_pct") is not None:
                lignes.append(f"Rendement actuel : {r['rendement_pct']:.2f}%")
            lignes.append("")
            note_s = r.get("note_sante")
            note_d = r.get("note_div")
            lignes.append(f"Note santé financière : {note_s}/10" if note_s is not None else "Note santé financière : N/A")
            lignes.append(f"Note fiabilité dividende : {note_d}/10" if note_d is not None else "Note fiabilité dividende : N/A (pas de dividende ou données insuffisantes)")
            lignes.append(f"Verdict : {r.get('verdict', '')}")

        self.resume_txt = "\n".join(lignes)
        self.notes_sante = r.get("notes_sante", [])
        self.notes_div = r.get("notes_div", [])
        self.notes_sante_txt = "\n".join(f"• {n}" for n in self.notes_sante) or "Données insuffisantes."
        self.notes_div_txt = "\n".join(f"• {n}" for n in self.notes_div) or "Données insuffisantes."

    def supprimer(self):
        if self._index is not None:
            storage.supprimer_position(self._index)
        self.manager.current = "portfolio"


class SettingsScreen(Screen):
    statut_txt = StringProperty("")
    statut_color = ListProperty([1, 1, 1, 1])

    def on_pre_enter(self):
        settings = storage.charger_settings()
        self.ids.url_input.text = settings.get("server_url", "")
        self.statut_txt = ""

    def tester(self):
        url = self.ids.url_input.text.strip()
        self.statut_txt = "Test en cours..."
        self.statut_color = (1, 1, 1, 1)
        threading.Thread(target=self._tester_en_fond, args=(url,), daemon=True).start()

    def _tester_en_fond(self, url):
        ok, message = api_client.tester_connexion(url)
        self._afficher_statut(ok, message)

    @mainthread
    def _afficher_statut(self, ok, message):
        self.statut_txt = message
        self.statut_color = (0.30, 0.80, 0.40, 1) if ok else (0.90, 0.30, 0.30, 1)

    def enregistrer(self):
        url = self.ids.url_input.text.strip()
        if url:
            storage.set_server_url(url)
            self.statut_txt = "Enregistré."
            self.statut_color = (0.30, 0.80, 0.40, 1)


class SuiviBourseApp(App):
    def build(self):
        Builder.load_string(KV)
        sm = ScreenManager()
        sm.add_widget(PortfolioScreen())
        sm.add_widget(AddPositionScreen())
        sm.add_widget(DetailScreen())
        sm.add_widget(SettingsScreen())
        return sm


if __name__ == "__main__":
    SuiviBourseApp().run()
