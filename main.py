#!/usr/bin/env python3
"""
main.py — Suivi Bourse (Kivy, client léger) — v3

Nouveautés vs v2 :
- Rafraîchissement progressif : chaque position affiche son résultat dès
  qu'il arrive (pool de threads + mise à jour ligne par ligne), au lieu
  d'attendre que TOUT le portefeuille soit chargé avant d'afficher quoi
  que ce soit.
- UI retravaillée : palette cohérente, cartes avec accent coloré selon
  PV/MV, hiérarchie visuelle plus claire.
- Écran Actualités par position (titres + sentiment basique + lien).

Toujours un client léger (kivy + requests) : la vraie logique tourne sur
server.py (voir README).
"""

import threading
import webbrowser
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date

from kivy.app import App
from kivy.clock import mainthread, Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.properties import StringProperty, ListProperty, BooleanProperty
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.label import Label
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.widget import Widget
from kivy.graphics import Color as GraphicsColor, Ellipse, Rectangle, Line, PushMatrix, PopMatrix, Rotate
from kivy.metrics import dp as dp_py
from kivy.factory import Factory

import storage
import api_client

BG = (0.07, 0.08, 0.10, 1)
CARD = (0.13, 0.145, 0.175, 1)
TXT_MUTED = (0.62, 0.65, 0.70, 1)
GREEN = (0.32, 0.78, 0.48, 1)
RED = (0.92, 0.38, 0.38, 1)
ORANGE = (0.95, 0.65, 0.25, 1)
WHITE = (0.95, 0.96, 0.97, 1)
BLEU_ACCENT = (0.184, 0.435, 0.929, 1)  # #2f6fed — tokens du design
PALETTE_AVATARS = [
    (0.184, 0.435, 0.929, 1),  # #2f6fed
    (0.949, 0.651, 0.247, 1),  # #f2a63f
    (0.322, 0.780, 0.478, 1),  # #52c77a
    (0.541, 0.361, 0.976, 1),  # #8a5cf6
]
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]


def date_fr_majuscules(date_iso):
    """'2026-09-18' -> '18 SEPTEMBRE 2026' (sans dépendre de la locale
    système, peu fiable sous Android/Buildozer)."""
    try:
        y, m, d = date_iso.split("-")
        return f"{int(d)} {MOIS_FR[int(m) - 1]} {y}".upper()
    except Exception:
        return date_iso


def initiales_depuis_nom(nom):
    mots = [w for w in nom.replace("-", " ").split(" ") if w]
    if not mots:
        return "??"
    if len(mots) == 1:
        return mots[0][:2].upper()
    return (mots[0][0] + mots[1][0]).upper()


def fenetre_dividendes():
    """Retourne (date_debut, date_fin) : du 1er jour du mois précédent le
    mois en cours, jusqu'au dernier jour du 4e mois suivant le mois en
    cours (borne exclusive). Ex. si on est en septembre 2026 :
    01/08/2026 -> 01/02/2027 (exclu), donc jusqu'à fin janvier 2027."""
    aujourdhui = date.today()

    annee_debut = aujourdhui.year if aujourdhui.month > 1 else aujourdhui.year - 1
    mois_debut = aujourdhui.month - 1 if aujourdhui.month > 1 else 12
    date_debut = date(annee_debut, mois_debut, 1)

    mois_total = aujourdhui.month + 4  # +4 mois après le mois en cours
    annee_fin = aujourdhui.year + (mois_total - 1) // 12
    mois_fin = (mois_total - 1) % 12 + 1
    date_fin = date(annee_fin, mois_fin, 1)  # borne exclusive

    return date_debut, date_fin

KV = """
#:import dp kivy.metrics.dp

<SectionLabel@Label>:
    bold: True
    font_size: "13sp"
    color: 0.62, 0.65, 0.70, 1
    size_hint_y: None
    height: dp(26)
    halign: "left"
    text_size: self.size

<PillButton@Button>:
    background_normal: ""
    background_color: 0.30, 0.62, 0.98, 1
    color: 1, 1, 1, 1
    bold: True
    font_size: "13sp"

<GhostButton@Button>:
    background_normal: ""
    background_color: 0.18, 0.20, 0.24, 1
    color: 0.85, 0.87, 0.90, 1
    font_size: "13sp"

<PositionRow@BoxLayout>:
    orientation: "horizontal"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(14), dp(8)
    spacing: dp(12)
    canvas.before:
        Color:
            rgba: 0.13, 0.145, 0.175, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: root.accent_color
        RoundedRectangle:
            pos: self.pos
            size: (dp(4), self.height)
            radius: [dp(2)]

    ticker: ""
    nom: ""
    prix_txt: ""
    quantite_txt: ""
    pv_mv_txt: "..."
    pv_mv_color: 0.62, 0.65, 0.70, 1
    accent_color: 0.30, 0.62, 0.98, 1
    sante_txt: "…"
    div_txt: "…"
    verdict: ""
    a_des_alertes: False
    initiales: "??"
    couleur_avatar: 0.184, 0.435, 0.929, 1

    AnchorLayout:
        size_hint_x: None
        width: dp(40)
        anchor_y: "top"
        padding: 0, dp(2), 0, 0
        AvatarCercle:
            initiales: root.initiales
            couleur: root.couleur_avatar

    BoxLayout:
        orientation: "vertical"
        size_hint_y: None
        height: self.minimum_height
        spacing: dp(3)

        BoxLayout:
            size_hint_y: None
            height: max(label_nom.texture_size[1], label_pvmv.texture_size[1])
            Label:
                id: label_nom
                text: root.nom + ("   [color=9fa3ab]" + root.prix_txt + "[/color]" if root.prix_txt else "")
                markup: True
                bold: True
                font_size: "15sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                valign: "top"
                text_size: self.width, None
                shorten: True
            Label:
                id: label_pvmv
                text: root.pv_mv_txt
                color: root.pv_mv_color
                bold: True
                font_size: "13sp"
                halign: "right"
                valign: "top"
                text_size: self.width, None
                size_hint_x: 0.5

        BoxLayout:
            size_hint_y: None
            height: label_qte.texture_size[1]
            Label:
                id: label_qte
                text: (root.quantite_txt + " actions" if root.quantite_txt else "")
                font_size: "12sp"
                color: 0.62, 0.65, 0.70, 1
                halign: "left"
                text_size: self.width, None

        BoxLayout:
            size_hint_y: None
            height: dp(18)
            spacing: dp(10)
            Label:
                text: "[color=9fa3ab]Santé[/color]  [b]" + root.sante_txt + "[/b]/10"
                markup: True
                font_size: "12sp"
                color: 0.85, 0.87, 0.90, 1
                halign: "left"
                size_hint_x: 0.85
                text_size: self.size
            Label:
                text: "[color=9fa3ab]Div[/color]  [b]" + root.div_txt + "[/b]/10"
                markup: True
                font_size: "12sp"
                color: 0.85, 0.87, 0.90, 1
                halign: "left"
                size_hint_x: 0.65
                text_size: self.size
            Label:
                markup: True
                text: ("[color=4caf50]" if root.verdict.startswith("OK") else "[color=e05555]" if root.verdict.startswith("KO") else "[color=f2a63f]" if root.verdict.startswith("MOYEN") else "[color=9fa3ab]") + (root.verdict.split(" ", 1)[-1] if " " in root.verdict else root.verdict) + "[/color]" + ("  [color=f2a63f][b]![/b][/color]" if root.a_des_alertes else "")
                font_size: "12sp"
                halign: "right"
                shorten: True
                shorten_from: "right"
                text_size: self.size

<NewsRow@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(14), dp(10)
    spacing: dp(4)
    canvas.before:
        Color:
            rgba: 0.13, 0.145, 0.175, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

    titre: ""
    editeur: ""
    date_txt: ""
    sentiment: "neutre"
    lien: ""
    alerte: False
    source: ""

    BoxLayout:
        size_hint_y: None
        height: dp(20) if root.alerte else 0
        opacity: 1 if root.alerte else 0
        Label:
            text: "! Actu récente pouvant impacter le cours" if root.alerte else ""
            font_size: "11sp"
            bold: True
            color: 0.95, 0.65, 0.25, 1
            halign: "left"
            text_size: self.size

    Label:
        text: root.titre
        bold: True
        font_size: "14sp"
        color: 0.95, 0.96, 0.97, 1
        size_hint_y: None
        height: self.texture_size[1]
        text_size: self.width, None
        halign: "left"

    BoxLayout:
        size_hint_y: None
        height: dp(22)
        spacing: dp(8)
        Label:
            text: root.editeur + ("  •  " + root.date_txt if root.date_txt else "")
            font_size: "11sp"
            color: 0.62, 0.65, 0.70, 1
            halign: "left"
            text_size: self.size
        Label:
            markup: True
            text: ("[color=4caf50]Positif[/color]" if root.sentiment == "positif" else "[color=e05555]Négatif[/color]" if root.sentiment == "negatif" else "[color=9fa3ab]Neutre[/color]")
            font_size: "11sp"
            halign: "right"
            text_size: self.size
            size_hint_x: 0.4

    BoxLayout:
        size_hint_y: None
        height: dp(18) if root.source else 0
        Label:
            text: root.source
            font_size: "10sp"
            color: 0.45, 0.48, 0.52, 1
            halign: "left"
            text_size: self.size

<AvatarCercle@BoxLayout>:
    size_hint: None, None
    size: dp(40), dp(40)
    couleur: 0.184, 0.435, 0.929, 1
    initiales: "??"
    canvas.before:
        Color:
            rgba: root.couleur
        Ellipse:
            pos: self.pos
            size: self.size
    Label:
        text: root.initiales
        color: 0.11, 0.13, 0.16, 1
        bold: True
        font_size: "12sp"

<DividendMonthGroup@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    replie: False
    nom_mois: ""
    total_mois: ""

    BoutonBoxLayout:
        id: entete
        orientation: "horizontal"
        size_hint_y: None
        height: dp(40)
        padding: dp(2), 0
        canvas.before:
            Color:
                rgba: 0.15, 0.17, 0.21, 1
            Line:
                points: [self.x, self.y, self.x + self.width, self.y]
                width: 1
        AnchorLayout:
            size_hint_x: None
            width: dp(24)
            IconChevron:
                id: icone_chevron
                size_hint: None, None
                size: dp(18), dp(18)
                direction: "up"
                couleur: 0.95, 0.96, 0.97, 1
        Label:
            text: root.nom_mois
            bold: True
            font_size: "16sp"
            color: 0.95, 0.96, 0.97, 1
            halign: "left"
            valign: "bottom"
            text_size: self.size
        Label:
            text: root.total_mois
            bold: True
            font_size: "14sp"
            color: 0.322, 0.780, 0.478, 1
            halign: "right"
            valign: "bottom"
            text_size: self.size
            size_hint_x: 0.4

    BoxLayout:
        id: contenu
        orientation: "vertical"
        size_hint_y: None
        height: self.minimum_height

<DividendGroupHeader@Label>:
    size_hint_y: None
    height: dp(30)
    bold: True
    font_size: "12sp"
    color: 0.48, 0.51, 0.56, 1
    halign: "left"
    valign: "bottom"
    text_size: self.size
    padding: dp(8), 0, 0, 0

<DividendRow@BoxLayout>:
    orientation: "horizontal"
    size_hint_y: None
    height: dp(60)
    padding: dp(4), dp(8)
    spacing: dp(12)
    nom: ""
    initiales: "??"
    couleur_avatar: 0.184, 0.435, 0.929, 1
    per_share_txt: ""
    qty_txt: ""
    total_txt: ""
    canvas.before:
        Color:
            rgba: 0.15, 0.17, 0.21, 1
        Line:
            points: [self.x, self.y, self.x + self.width, self.y]
            width: 1
    AvatarCercle:
        initiales: root.initiales
        couleur: root.couleur_avatar
    BoxLayout:
        orientation: "vertical"
        Label:
            text: root.nom
            bold: True
            font_size: "14sp"
            color: 0.95, 0.96, 0.97, 1
            halign: "left"
            valign: "bottom"
            text_size: self.size
            shorten: True
            size_hint_y: 0.55
        Label:
            text: root.per_share_txt + " / action  ·  " + root.qty_txt
            font_size: "11sp"
            color: 0.48, 0.51, 0.56, 1
            halign: "left"
            valign: "top"
            text_size: self.size
            size_hint_y: 0.45
    Label:
        text: root.total_txt
        bold: True
        font_size: "15sp"
        color: 0.322, 0.780, 0.478, 1
        halign: "right"
        valign: "middle"
        text_size: self.size
        size_hint_x: 0.34

<PortfolioScreen>:
    name: "portfolio"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(96)
            padding: dp(20), dp(28), dp(20), dp(14)
            spacing: dp(4)
            orientation: "vertical"
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            Label:
                text: "Valeur du portefeuille"
                font_size: "13sp"
                bold: True
                color: 0.482, 0.51, 0.564, 1
                halign: "left"
                text_size: self.size
                size_hint_y: None
                height: dp(18)
            BoxLayout:
                size_hint_y: None
                height: dp(40)
                spacing: dp(10)
                Label:
                    text: root.total_valeur_txt
                    font_size: "28sp"
                    bold: True
                    color: 0.953, 0.961, 0.969, 1
                    halign: "left"
                    valign: "middle"
                    text_size: self.size
                AnchorLayout:
                    size_hint_x: None
                    width: label_pv.texture_size[0] + dp(20)
                    anchor_y: "center"
                    BoxLayout:
                        size_hint: None, None
                        size: label_pv.texture_size[0] + dp(20), dp(26)
                        canvas.before:
                            Color:
                                rgba: root.total_badge_bg
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [dp(13)]
                        Label:
                            id: label_pv
                            text: root.total_txt
                            font_size: "13sp"
                            bold: True
                            color: root.total_color
                            size_hint: None, None
                            size: self.texture_size
                            pos_hint: {"center_x": 0.5, "center_y": 0.5}
            Label:
                text: ("MàJ " + root.derniere_maj) if root.derniere_maj else ""
                font_size: "11sp"
                color: 0.482, 0.51, 0.564, 1
                halign: "left"
                text_size: self.size
                size_hint_y: None
                height: dp(14)

        BoxLayout:
            size_hint_y: None
            height: dp(48)
            padding: dp(10), dp(6)
            spacing: dp(8)
            PillButton:
                text: "+ Ajouter"
                on_release: root.manager.current = "add"
            GhostButton:
                text: "Rafraîchir"
                disabled: root.refreshing
                on_release: root.rafraichir()
            GhostButton:
                text: ""
                size_hint_x: 0.3
                on_release: root.manager.current = "settings"
                AnchorLayout:
                    size: self.parent.size
                    pos: self.parent.pos
                    IconEngrenage:
                        size_hint: None, None
                        size: dp(22), dp(22)
                        couleur: 0.85, 0.87, 0.90, 1
                        couleur_fond: 0.18, 0.20, 0.24, 1

        BoxLayout:
            size_hint_y: None
            height: dp(40)
            padding: dp(20), 0, dp(20), 0
            canvas.before:
                Color:
                    rgba: 0.15, 0.17, 0.21, 1
                Line:
                    points: [self.x, self.y, self.x + self.width, self.y]
                    width: 1
            Button:
                text: "Portefeuille"
                background_color: 0, 0, 0, 0
                background_normal: ""
                background_down: ""
                color: (0.95, 0.96, 0.97, 1) if root.tab_actif == "portefeuille" else (0.48, 0.51, 0.56, 1)
                bold: True
                font_size: "14sp"
                on_release: root.changer_tab("portefeuille")
                canvas.after:
                    Color:
                        rgba: (0.184, 0.435, 0.929, 1) if root.tab_actif == "portefeuille" else (0, 0, 0, 0)
                    Rectangle:
                        pos: self.x, self.y
                        size: self.width, dp(2)
            Button:
                text: "Dividendes"
                background_color: 0, 0, 0, 0
                background_normal: ""
                background_down: ""
                color: (0.95, 0.96, 0.97, 1) if root.tab_actif == "dividendes" else (0.48, 0.51, 0.56, 1)
                bold: True
                font_size: "14sp"
                on_release: root.changer_tab("dividendes")
                canvas.after:
                    Color:
                        rgba: (0.184, 0.435, 0.929, 1) if root.tab_actif == "dividendes" else (0, 0, 0, 0)
                    Rectangle:
                        pos: self.x, self.y
                        size: self.width, dp(2)

        Label:
            text: root.erreur_globale
            color: 0.95, 0.65, 0.25, 1
            size_hint_y: None
            height: dp(28) if root.erreur_globale else 0
            font_size: "12sp"

        ScrollView:
            size_hint_y: None if root.tab_actif != "portefeuille" else 1
            height: 0 if root.tab_actif != "portefeuille" else dp(1)
            opacity: 1 if root.tab_actif == "portefeuille" else 0
            disabled: root.tab_actif != "portefeuille"
            BoxLayout:
                id: liste_box
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(10), dp(4)
                spacing: dp(8)

        ScrollView:
            size_hint_y: None if root.tab_actif != "dividendes" else 1
            height: 0 if root.tab_actif != "dividendes" else dp(1)
            opacity: 1 if root.tab_actif == "dividendes" else 0
            disabled: root.tab_actif != "dividendes"
            BoxLayout:
                id: dividendes_box
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(10), dp(4)
                spacing: dp(2)

<AddPositionScreen>:
    name: "add"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"
        padding: dp(24)
        spacing: dp(14)

        Label:
            text: "Nouvelle position"
            font_size: "20sp"
            bold: True
            color: 0.95, 0.96, 0.97, 1
            size_hint_y: None
            height: dp(40)
            halign: "left"
            text_size: self.size

        TextInput:
            id: ticker_input
            hint_text: "Ticker (ex: MC.PA, AAPL, TTE.PA)"
            multiline: False
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        TextInput:
            id: quantite_input
            hint_text: "Quantité"
            multiline: False
            input_filter: "float"
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        TextInput:
            id: pru_input
            hint_text: "Prix de revient unitaire (PRU)"
            multiline: False
            input_filter: "float"
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        Label:
            id: erreur_label
            text: ""
            color: 0.92, 0.38, 0.38, 1
            size_hint_y: None
            height: dp(26)
            font_size: "12sp"

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: dp(10)
            GhostButton:
                text: "Annuler"
                on_release: root.annuler()
            PillButton:
                text: "Ajouter"
                on_release: root.ajouter()

        SectionLabel:
            text: "OU"

        Label:
            id: import_statut_label
            text: root.import_statut_txt
            color: root.import_statut_color
            size_hint_y: None
            height: dp(26) if root.import_statut_txt else 0
            font_size: "12sp"

        GhostButton:
            text: "⇩ Importer depuis Trading212"
            size_hint_y: None
            height: dp(50)
            disabled: root.import_en_cours
            on_release: root.importer_t212()

        Widget:

<NoteTile@BoxLayout>:
    orientation: "horizontal"
    size_hint_y: None
    height: max(dp(34), label_note.texture_size[1] + dp(16))
    padding: dp(12), dp(8)
    spacing: dp(8)
    texte: ""
    canvas.before:
        Color:
            rgba: 0.13, 0.145, 0.175, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
    Label:
        id: label_note
        text: root.texte
        font_size: "13sp"
        color: 0.78, 0.80, 0.82, 1
        halign: "left"
        valign: "middle"
        text_size: self.width, None
        size_hint_y: None
        height: self.texture_size[1]

<DetailScreen>:
    name: "detail"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(12), dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            GhostButton:
                text: ""
                size_hint_x: 0.15
                on_release: root.manager.current = "portfolio"
                AnchorLayout:
                    size: self.parent.size
                    pos: self.parent.pos
                    IconChevron:
                        size_hint: None, None
                        size: dp(20), dp(20)
                        direction: "left"
                        couleur: 0.78, 0.80, 0.82, 1
            Label:
                text: root.nom
                bold: True
                font_size: "17sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                valign: "middle"
                text_size: self.size
                shorten: True

        ScrollView:
            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(18)
                spacing: dp(16)

                BoxLayout:
                    orientation: "vertical"
                    size_hint_y: None
                    height: dp(116)
                    padding: dp(14)
                    spacing: dp(4)
                    canvas.before:
                        Color:
                            rgba: 0.11, 0.125, 0.16, 1
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [dp(14)]
                    Label:
                        text: "Quantité détenue"
                        font_size: "13sp"
                        color: 0.42, 0.45, 0.50, 1
                        halign: "left"
                        text_size: self.size
                        size_hint_y: None
                        height: dp(18)
                    Label:
                        text: root.quantite_detail_txt
                        font_size: "15sp"
                        bold: True
                        color: 0.95, 0.96, 0.97, 1
                        halign: "left"
                        text_size: self.size
                        size_hint_y: None
                        height: dp(20)
                    BoxLayout:
                        size_hint_y: None
                        height: dp(60)
                        BoxLayout:
                            orientation: "vertical"
                            Label:
                                text: "Prix actuel"
                                font_size: "13sp"
                                color: 0.42, 0.45, 0.50, 1
                                halign: "left"
                                valign: "top"
                                text_size: self.size
                                size_hint_y: None
                                height: dp(18)
                            Label:
                                text: root.prix_actuel_txt
                                font_size: "18sp"
                                bold: True
                                color: 0.95, 0.96, 0.97, 1
                                halign: "left"
                                valign: "top"
                                text_size: self.size
                        BoxLayout:
                            orientation: "vertical"
                            Label:
                                text: "PV / MV"
                                font_size: "13sp"
                                color: 0.42, 0.45, 0.50, 1
                                halign: "right"
                                valign: "top"
                                text_size: self.size
                                size_hint_y: None
                                height: dp(18)
                            Label:
                                text: root.pv_mv_detail_txt
                                font_size: "15sp"
                                bold: True
                                color: root.pv_mv_detail_color
                                halign: "right"
                                valign: "top"
                                text_size: self.size

                BoxLayout:
                    size_hint_y: None
                    height: dp(78)
                    spacing: dp(12)
                    BoxLayout:
                        orientation: "vertical"
                        padding: dp(12)
                        canvas.before:
                            Color:
                                rgba: 0.11, 0.125, 0.16, 1
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [dp(14)]
                        Label:
                            text: "Santé financière"
                            font_size: "12sp"
                            color: 0.42, 0.45, 0.50, 1
                            halign: "center"
                            text_size: self.size
                            size_hint_y: 0.4
                        Label:
                            markup: True
                            text: "[b]" + root.note_sante_txt + "[/b]  [size=13sp][color=7b8290]/10[/color][/size]"
                            font_size: "26sp"
                            color: 0.95, 0.96, 0.97, 1
                            halign: "center"
                            valign: "middle"
                            text_size: self.size
                            size_hint_y: 0.6
                    BoxLayout:
                        orientation: "vertical"
                        padding: dp(12)
                        canvas.before:
                            Color:
                                rgba: 0.11, 0.125, 0.16, 1
                            RoundedRectangle:
                                pos: self.pos
                                size: self.size
                                radius: [dp(14)]
                        Label:
                            text: "Fiabilité dividende"
                            font_size: "12sp"
                            color: 0.42, 0.45, 0.50, 1
                            halign: "center"
                            text_size: self.size
                            size_hint_y: 0.4
                        Label:
                            markup: True
                            text: "[b]" + root.note_div_txt + "[/b]  [size=13sp][color=7b8290]/10[/color][/size]"
                            font_size: "26sp"
                            color: 0.95, 0.96, 0.97, 1
                            halign: "center"
                            valign: "middle"
                            text_size: self.size
                            size_hint_y: 0.6

                BoxLayout:
                    size_hint_y: None
                    height: dp(44) if root.verdict_txt else 0
                    padding: dp(14), dp(10)
                    canvas.before:
                        Color:
                            rgba: root.verdict_bg
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [dp(12)]
                    Label:
                        text: root.verdict_txt
                        bold: True
                        font_size: "13sp"
                        color: root.verdict_color
                        halign: "left"
                        valign: "middle"
                        text_size: self.size

                Label:
                    text: root.alertes_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1] if root.alertes_txt else 0
                    text_size: self.width, None
                    halign: "left"

                SectionLabel:
                    text: "POINTS CLÉS — SANTÉ FINANCIÈRE"
                    height: dp(26) if root.notes_sante else 0

                BoxLayout:
                    id: notes_sante_box
                    orientation: "vertical"
                    size_hint_y: None
                    height: self.minimum_height
                    spacing: dp(6)

                SectionLabel:
                    text: "POINTS CLÉS — FIABILITÉ DIVIDENDE"
                    height: dp(26) if root.notes_div else 0

                BoxLayout:
                    id: notes_div_box
                    orientation: "vertical"
                    size_hint_y: None
                    height: self.minimum_height
                    spacing: dp(6)

                SectionLabel:
                    text: "ANALYSE TECHNIQUE"
                    height: dp(26) if root.technique_txt else 0

                Label:
                    text: root.technique_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.80, 0.83, 0.86, 1

                Label:
                    text: root.resume_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.55, 0.58, 0.62, 1
                    font_size: "12sp"

                BoxLayout:
                    size_hint_y: None
                    height: dp(46)
                    spacing: dp(10)
                    padding: 0, dp(4), 0, 0
                    PillButton:
                        text: "Actus"
                        on_release: root.ouvrir_actualites()
                    GhostButton:
                        text: "Analystes"
                        on_release: root.ouvrir_analystes()

                GhostButton:
                    text: "Supprimer la position"
                    color: 0.92, 0.38, 0.38, 1
                    background_color: 0, 0, 0, 0
                    size_hint_y: None
                    height: dp(44)
                    on_release: root.supprimer()

<NewsScreen>:
    name: "news"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(64)
            padding: dp(12), dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            GhostButton:
                text: ""
                size_hint_x: 0.15
                on_release: root.manager.current = "detail"
                AnchorLayout:
                    size: self.parent.size
                    pos: self.parent.pos
                    IconChevron:
                        size_hint: None, None
                        size: dp(20), dp(20)
                        direction: "left"
                        couleur: 0.78, 0.80, 0.82, 1
            Label:
                text: "Actualités — " + root.nom
                bold: True
                font_size: "16sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                text_size: self.size

        Label:
            text: root.statut_txt
            color: 0.62, 0.65, 0.70, 1
            size_hint_y: None
            height: dp(30) if root.statut_txt else 0
            font_size: "12sp"

        ScrollView:
            BoxLayout:
                id: news_box
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(10), dp(4)
                spacing: dp(8)

<AnalystesScreen>:
    name: "analystes"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"

        BoxLayout:
            size_hint_y: None
            height: dp(64)
            padding: dp(12), dp(10)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.10, 0.11, 0.14, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            GhostButton:
                text: ""
                size_hint_x: 0.15
                on_release: root.manager.current = "detail"
                AnchorLayout:
                    size: self.parent.size
                    pos: self.parent.pos
                    IconChevron:
                        size_hint: None, None
                        size: dp(20), dp(20)
                        direction: "left"
                        couleur: 0.78, 0.80, 0.82, 1
            Label:
                text: "Analystes — " + root.nom
                bold: True
                font_size: "16sp"
                color: 0.95, 0.96, 0.97, 1
                halign: "left"
                text_size: self.size

        Label:
            text: root.statut_txt
            color: 0.62, 0.65, 0.70, 1
            size_hint_y: None
            height: dp(30) if root.statut_txt else 0
            font_size: "12sp"

        ScrollView:
            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: dp(16)
                spacing: dp(12)

                Label:
                    text: root.contenu_txt
                    markup: True
                    size_hint_y: None
                    height: self.texture_size[1]
                    text_size: self.width, None
                    halign: "left"
                    color: 0.90, 0.92, 0.94, 1

                Label:
                    text: root.source_txt
                    font_size: "11sp"
                    color: 0.45, 0.48, 0.52, 1
                    size_hint_y: None
                    height: self.texture_size[1] if root.source_txt else 0
                    text_size: self.width, None
                    halign: "left"

<SettingsScreen>:
    name: "settings"
    canvas.before:
        Color:
            rgba: 0.07, 0.08, 0.10, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: "vertical"
        padding: dp(24)
        spacing: dp(14)

        Label:
            text: "Paramètres"
            font_size: "20sp"
            bold: True
            color: 0.95, 0.96, 0.97, 1
            size_hint_y: None
            height: dp(40)
            halign: "left"
            text_size: self.size

        SectionLabel:
            text: "ADRESSE DU SERVEUR (server.py)"

        TextInput:
            id: url_input
            hint_text: "http://192.168.1.X:8765"
            multiline: False
            size_hint_y: None
            height: dp(50)
            background_color: 0.13, 0.145, 0.175, 1
            foreground_color: 1, 1, 1, 1
            hint_text_color: 0.55, 0.58, 0.62, 1
            padding: dp(12), dp(14)

        Label:
            id: statut_label
            text: root.statut_txt
            color: root.statut_color
            size_hint_y: None
            height: dp(30)
            font_size: "13sp"

        BoxLayout:
            size_hint_y: None
            height: dp(50)
            spacing: dp(10)
            GhostButton:
                text: "Tester"
                on_release: root.tester()
            PillButton:
                text: "Enregistrer"
                on_release: root.enregistrer()

        Label:
            text:
                ("Cette app ne fait tourner aucun calcul localement : elle "
                "interroge un petit serveur (server.py) lancé sur ton PC, "
                "ton VPS, ou Termux. Renseigne ici son adresse IP et son "
                "port (8765 par défaut).")
            size_hint_y: None
            height: self.texture_size[1]
            text_size: self.width, None
            halign: "left"
            font_size: "12sp"
            color: 0.55, 0.58, 0.62, 1

        Widget:

        GhostButton:
            text: "< Retour au portefeuille"
            size_hint_y: None
            height: dp(50)
            on_release: root.manager.current = "portfolio"
"""



class BoutonBoxLayout(ButtonBehavior, BoxLayout):
    """BoxLayout avec comportement bouton (cycle presse/relâche fiable,
    y compris dans un ScrollView). Utilisé pour l'en-tête de mois
    repliable de l'onglet Dividendes, qui a besoin à la fois d'un
    agencement horizontal (icône + libellés) ET d'un vrai on_release —
    aucune classe Kivy de base ne combine les deux."""
    pass


class IconEngrenage(Widget):
    """Roue crantée dessinée en vectoriel (Color/Ellipse/Rectangle), pas un
    caractère de police — les caractères comme ⚙ ne sont pas dans la
    police embarquée par Buildozer sur Android et s'affichent en carré
    vide (même souci que les emoji ailleurs dans l'app)."""
    couleur = ListProperty([0.85, 0.87, 0.90, 1])
    couleur_fond = ListProperty([0.18, 0.20, 0.24, 1])  # couleur du bouton, pour "percer" le centre

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redessiner, size=self._redessiner,
                  couleur=self._redessiner, couleur_fond=self._redessiner)

    def _redessiner(self, *args):
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        cx, cy = self.center_x, self.center_y
        rayon_ext = min(self.width, self.height) * 0.46
        rayon_corps = rayon_ext * 0.62
        rayon_trou = rayon_ext * 0.30
        largeur_dent = rayon_ext * 0.34
        nb_dents = 8
        with self.canvas:
            GraphicsColor(*self.couleur)
            Ellipse(pos=(cx - rayon_corps, cy - rayon_corps), size=(rayon_corps * 2, rayon_corps * 2))
            for i in range(nb_dents):
                angle_deg = 360.0 * i / nb_dents
                PushMatrix()
                Rotate(angle=angle_deg, origin=(cx, cy))
                Rectangle(pos=(cx - largeur_dent / 2, cy + rayon_corps * 0.55),
                          size=(largeur_dent, rayon_ext - rayon_corps * 0.55))
                PopMatrix()
            GraphicsColor(*self.couleur_fond)
            Ellipse(pos=(cx - rayon_trou, cy - rayon_trou), size=(rayon_trou * 2, rayon_trou * 2))


Factory.register("IconEngrenage", cls=IconEngrenage)
Factory.register("BoutonBoxLayout", cls=BoutonBoxLayout)


class IconChevron(Widget):
    """Chevron simple (angle ouvert, sans tige) dessiné en vectoriel.
    direction: 'left' (retour), 'right', 'up', 'down' (accordéons)."""
    couleur = ListProperty([0.78, 0.80, 0.82, 1])
    direction = StringProperty("left")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bind(pos=self._redessiner, size=self._redessiner,
                  couleur=self._redessiner, direction=self._redessiner)

    def _redessiner(self, *args):
        self.canvas.clear()
        if self.width <= 0 or self.height <= 0:
            return
        cx, cy = self.center_x, self.center_y
        t = min(self.width, self.height) * 0.30
        if self.direction == "left":
            points = [cx + t * 0.5, cy + t, cx - t * 0.5, cy, cx + t * 0.5, cy - t]
        elif self.direction == "right":
            points = [cx - t * 0.5, cy + t, cx + t * 0.5, cy, cx - t * 0.5, cy - t]
        elif self.direction == "down":
            points = [cx - t, cy + t * 0.5, cx, cy - t * 0.5, cx + t, cy + t * 0.5]
        else:  # up
            points = [cx - t, cy - t * 0.5, cx, cy + t * 0.5, cx + t, cy - t * 0.5]
        with self.canvas:
            GraphicsColor(*self.couleur)
            Line(points=points, width=dp_py(1.6), cap="round", joint="round")


Factory.register("IconChevron", cls=IconChevron)


def couleur_pv(valeur):
    if valeur is None:
        return TXT_MUTED
    if valeur > 0:
        return GREEN
    if valeur < 0:
        return RED
    return WHITE


class PortfolioScreen(Screen):
    total_txt = StringProperty("")
    total_color = ListProperty(list(WHITE))
    total_valeur_txt = StringProperty("")
    total_badge_bg = ListProperty([0, 0, 0, 0])
    refreshing = BooleanProperty(False)
    erreur_globale = StringProperty("")
    derniere_maj = StringProperty("")
    tab_actif = StringProperty("portefeuille")

    INTERVALLE_AUTO_REFRESH = 300  # secondes (5 minutes)
    _auto_refresh_event = None
    _dividendes_charges = False
    _mois_replies = None  # set de (année, mois) repliés, initialisé au premier accès
    _noms_par_ticker = None  # dict ticker -> nom, peuplé au fil des rafraîchissements

    def on_pre_enter(self):
        if self._noms_par_ticker is None:
            self._noms_par_ticker = {}
        self.rafraichir()

    def on_enter(self):
        # Auto-rafraîchissement tant que cet écran est affiché ; annulé
        # dans on_leave pour ne pas continuer à interroger le serveur en
        # arrière-plan une fois qu'on a quitté l'écran portefeuille.
        if self._auto_refresh_event is None:
            self._auto_refresh_event = Clock.schedule_interval(
                lambda dt: self.rafraichir(), self.INTERVALLE_AUTO_REFRESH
            )

    def on_leave(self):
        if self._auto_refresh_event is not None:
            self._auto_refresh_event.cancel()
            self._auto_refresh_event = None

    def rafraichir(self):
        if self.refreshing:
            return
        positions = storage.charger_positions()
        self.ids.liste_box.clear_widgets()
        self._rows = []
        self._total_pv = 0.0
        self._total_valeur = 0.0
        self._total_connu = False
        self._erreurs = 0

        if not positions:
            self.ids.liste_box.add_widget(
                Label(text="Aucune position. Appuie sur + Ajouter.",
                      size_hint_y=None, height=80, color=TXT_MUTED)
            )
            self.total_txt = ""
            self.total_valeur_txt = "0.00 €"
            self.total_badge_bg = [0, 0, 0, 0]
            return

        self.refreshing = True
        self.erreur_globale = ""

        for pos in positions:
            row = Factory.PositionRow()
            row.ticker = pos["ticker"]
            row.nom = pos["ticker"]
            row.pv_mv_txt = "…"
            quantite = pos.get("quantite")
            if quantite is not None:
                # Affichage sans décimales inutiles si quantité entière
                row.quantite_txt = f"{quantite:g}"
            self.ids.liste_box.add_widget(row)
            self._rows.append(row)

        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        threading.Thread(target=self._lancer_pool, args=(positions, server_url), daemon=True).start()

    def _lancer_pool(self, positions, server_url):
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(api_client.analyser_position, server_url,
                                 pos["ticker"], pos["quantite"], pos["pru"]): i
                for i, pos in enumerate(positions)
            }
            for future in futures:
                i = futures[future]
                try:
                    resultat = future.result()
                except Exception as e:
                    resultat = {"ticker": positions[i]["ticker"], "erreur": str(e)}
                self._maj_ligne(i, resultat)
        self._finaliser()

    @mainthread
    def _maj_ligne(self, index, r):
        if index >= len(self._rows):
            return
        row = self._rows[index]
        row.ticker = r.get("ticker", row.ticker)
        row.nom = r.get("nom") or row.ticker
        row.initiales = initiales_depuis_nom(row.nom)
        # Somme des codes caractères plutôt que hash() : hash() est
        # randomisé par process en Python (PYTHONHASHSEED), la couleur
        # changerait à chaque redémarrage de l'app sinon.
        indice_couleur = sum(ord(c) for c in row.ticker) % len(PALETTE_AVATARS)
        row.couleur_avatar = PALETTE_AVATARS[indice_couleur]
        if self._noms_par_ticker is not None:
            self._noms_par_ticker[row.ticker.upper()] = row.nom

        prix = r.get("prix_actuel")
        devise = r.get("devise") or ""
        row.prix_txt = f"{prix:.2f} {devise}".strip() if prix is not None else ""

        if r.get("erreur"):
            err = r["erreur"] or ""
            if "injoignable" in err or "timeout" in err.lower():
                self._erreurs += 1
            row.pv_mv_txt = "Erreur"
            row.pv_mv_color = ORANGE
            row.accent_color = ORANGE
        elif r.get("pv_mv_eur") is not None:
            self._total_pv += r["pv_mv_eur"]
            self._total_connu = True
            if r.get("valeur_position") is not None:
                self._total_valeur += r["valeur_position"]
            signe = "+" if r["pv_mv_eur"] >= 0 else ""
            pct = r.get("pv_mv_pct")
            pct_txt = f"({signe}{pct:.1f}%)" if pct is not None else ""
            # Saut de ligne explicite plutôt que de laisser le retour à la
            # ligne automatique décider : hauteur de tuile prévisible sur
            # 2 lignes fixes, au lieu d'un wrap qui pouvait déborder sur
            # la ligne du dessous (montant tronqué).
            row.pv_mv_txt = f"{signe}{r['pv_mv_eur']:.2f} €" + (f"\n{pct_txt}" if pct_txt else "")
            row.pv_mv_color = couleur_pv(r["pv_mv_eur"])
            row.accent_color = couleur_pv(r["pv_mv_eur"])
        else:
            row.pv_mv_txt = "N/A"

        row.sante_txt = f"{r['note_sante']}" if r.get("note_sante") is not None else "N/A"
        row.div_txt = f"{r['note_div']}" if r.get("note_div") is not None else "N/A"
        row.verdict = r.get("verdict", "")
        row.a_des_alertes = bool(r.get("alertes"))

        row.bind(on_touch_up=lambda inst, touch, res=r:
                  self._ouvrir_detail(res) if inst.collide_point(*touch.pos) else None)

        signe_total = "+" if self._total_pv >= 0 else ""
        pct_total = (self._total_pv / (self._total_valeur - self._total_pv) * 100
                     if self._total_connu and (self._total_valeur - self._total_pv) else None)
        pct_total_txt = f" ({signe_total}{pct_total:.1f}%)" if pct_total is not None else ""
        self.total_txt = (f"{signe_total}{self._total_pv:.2f} €{pct_total_txt}"
                           if self._total_connu else "")
        self.total_color = list(couleur_pv(self._total_pv if self._total_connu else None))
        self.total_valeur_txt = f"{self._total_valeur:.2f} €" if self._total_connu else "…"
        if self._total_connu:
            self.total_badge_bg = ([0.322, 0.780, 0.478, 0.14] if self._total_pv >= 0
                                    else [0.918, 0.380, 0.380, 0.14])
        else:
            self.total_badge_bg = [0, 0, 0, 0]

    @mainthread
    def _finaliser(self):
        self.refreshing = False
        if self._erreurs and self._erreurs == len(self._rows):
            settings = storage.charger_settings()
            self.erreur_globale = f"Serveur injoignable à {settings.get('server_url', '')} — vérifie Paramètres."
        else:
            self.erreur_globale = ""
        self.derniere_maj = datetime.now().strftime("%H:%M")
        # Les dividendes réutilisent les noms résolus ci-dessus (via
        # _noms_par_ticker) : on ne les recharge que si l'onglet a déjà
        # été ouvert au moins une fois, pour rafraîchir le calendrier en
        # même temps que le reste plutôt que de le laisser périmer.
        if self._dividendes_charges:
            self.charger_dividendes()

    def changer_tab(self, tab):
        if self.tab_actif == tab:
            return
        self.tab_actif = tab
        if tab == "dividendes" and not self._dividendes_charges:
            self.charger_dividendes()

    def charger_dividendes(self):
        positions = storage.charger_positions()
        tickers = [p["ticker"] for p in positions if p.get("ticker")]
        if not tickers:
            self._afficher_dividendes({}, positions)
            return
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")

        def tache():
            data = api_client.obtenir_dividendes(server_url, tickers)
            self._afficher_dividendes(data, positions)

        threading.Thread(target=tache, daemon=True).start()

    @mainthread
    def _afficher_dividendes(self, data, positions):
        self._dividendes_charges = True
        if self._mois_replies is None:
            self._mois_replies = set()
        box = self.ids.dividendes_box
        box.clear_widgets()

        par_ticker = {p["ticker"].upper(): p for p in positions if p.get("ticker")}
        noms = self._noms_par_ticker or {}
        # Palette stable par ticker (même couleur d'avatar partout dans
        # l'app pour un même titre), pas juste par ordre d'apparition.
        tickers_tries = sorted(par_ticker.keys())
        couleur_par_ticker = {
            t: PALETTE_AVATARS[i % len(PALETTE_AVATARS)] for i, t in enumerate(tickers_tries)
        }

        date_debut, date_fin = fenetre_dividendes()

        evenements_par_date = {}
        for ticker, liste in (data or {}).items():
            cle = ticker.upper()
            pos = par_ticker.get(cle)
            if not pos:
                continue
            quantite = pos.get("quantite") or 0
            for e in liste:
                date_iso = e.get("date")
                montant = e.get("montant")
                if not date_iso or montant is None:
                    continue
                try:
                    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
                    if d < date_debut or d >= date_fin:
                        continue  # hors fenêtre : mois précédent -> 4 mois après le mois en cours
                except ValueError:
                    continue
                evenements_par_date.setdefault(date_iso, []).append({
                    "ticker": cle,
                    "nom": noms.get(cle, cle),
                    "montant": montant,
                    "quantite": quantite,
                    "prevu": bool(e.get("prevu")),
                })

        if not evenements_par_date:
            box.add_widget(Label(
                text="Aucun versement de dividende sur cette période.",
                size_hint_y=None, height=80, color=TXT_MUTED,
            ))
            return

        # Regroupement à deux niveaux : mois (ex. "SEPTEMBRE 2026") puis
        # jour (ex. "18 SEPTEMBRE 2026") à l'intérieur de chaque mois.
        dates_triees = sorted(evenements_par_date.keys())

        # Pré-calcul du total perçu par mois (montant × quantité, tous
        # tickers confondus), affiché à côté du libellé du mois.
        total_par_mois = {}
        for date_iso in dates_triees:
            annee, mois, _ = date_iso.split("-")
            cle_mois = (annee, mois)
            for e in evenements_par_date[date_iso]:
                total_par_mois[cle_mois] = total_par_mois.get(cle_mois, 0.0) + e["montant"] * e["quantite"]

        mois_courant_affiche = None
        groupe_mois = None

        for date_iso in dates_triees:
            annee, mois, _ = date_iso.split("-")
            cle_mois = (annee, mois)
            if cle_mois != mois_courant_affiche:
                mois_courant_affiche = cle_mois
                groupe_mois = Factory.DividendMonthGroup()
                groupe_mois.nom_mois = f"{MOIS_FR[int(mois) - 1]} {annee}".upper()
                groupe_mois.total_mois = f"+{total_par_mois[cle_mois]:.2f} €"
                groupe_mois._cle_mois = cle_mois
                groupe_mois.ids.entete.bind(
                    on_release=lambda inst, gm=groupe_mois: self._basculer_mois(gm)
                )
                box.add_widget(groupe_mois)
                # État replié/déplié mémorisé d'un rafraîchissement à
                # l'autre — appliqué APRÈS ajout au parent, une fois que
                # contenu.minimum_height peut déjà être calculé pour les
                # jours/lignes qu'on va y ajouter juste après.
                if cle_mois in self._mois_replies:
                    self._replier_mois(groupe_mois, replie=True, silencieux=True)

            entete_jour = Factory.DividendGroupHeader()
            libelle = date_fr_majuscules(date_iso)
            if evenements_par_date[date_iso][0]["prevu"]:
                libelle += "  (PRÉVU)"
            entete_jour.text = libelle
            groupe_mois.ids.contenu.add_widget(entete_jour)

            for e in sorted(evenements_par_date[date_iso], key=lambda x: x["nom"]):
                row = Factory.DividendRow()
                row.nom = e["nom"]
                row.initiales = initiales_depuis_nom(e["nom"])
                row.couleur_avatar = couleur_par_ticker.get(e["ticker"], PALETTE_AVATARS[0])
                row.per_share_txt = f"{e['montant']:.2f} €"
                qte = e["quantite"]
                qte_txt = f"{qte:g}" if qte else "0"
                row.qty_txt = f"{qte_txt} actions"
                total = e["montant"] * qte
                row.total_txt = f"+{total:.2f} €"
                groupe_mois.ids.contenu.add_widget(row)

            # Si ce groupe doit démarrer replié, on réapplique la hauteur
            # maintenant que toutes ses lignes du dernier jour ajouté sont
            # en place (minimum_height n'était pas encore final plus haut).
            if groupe_mois.replie:
                self._replier_mois(groupe_mois, replie=True, silencieux=True)

    def _basculer_mois(self, groupe_mois):
        """Appelé au clic sur l'en-tête d'un mois. Pilote directement les
        widgets en Python plutôt que de compter sur des bindings kv
        réactifs (`root.replie`) — plus fiable, notamment dans un
        ScrollView où on a eu des soucis de réactivité."""
        self._replier_mois(groupe_mois, replie=not groupe_mois.replie)

    def _replier_mois(self, groupe_mois, replie, silencieux=False):
        groupe_mois.replie = replie
        contenu = groupe_mois.ids.contenu
        if replie:
            contenu.height = 0
            contenu.opacity = 0
            contenu.disabled = True
        else:
            contenu.height = contenu.minimum_height
            contenu.opacity = 1
            contenu.disabled = False
        icone = groupe_mois.ids.get("icone_chevron")
        if icone is not None:
            icone.direction = "down" if replie else "up"
        if not silencieux:
            cle_mois = getattr(groupe_mois, "_cle_mois", None)
            if cle_mois is not None:
                if replie:
                    self._mois_replies.add(cle_mois)
                else:
                    self._mois_replies.discard(cle_mois)

    def _ouvrir_detail(self, resultat):
        detail = self.manager.get_screen("detail")
        detail.charger(resultat)
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "detail"


class AddPositionScreen(Screen):
    import_statut_txt = StringProperty("")
    import_statut_color = ListProperty(list(WHITE))
    import_en_cours = BooleanProperty(False)

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

    def importer_t212(self):
        self.import_en_cours = True
        self.import_statut_txt = "Import en cours..."
        self.import_statut_color = list(WHITE)
        threading.Thread(target=self._importer_t212_en_fond, daemon=True).start()

    def _importer_t212_en_fond(self):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        positions, erreur = api_client.obtenir_portefeuille_t212(server_url)
        self._traiter_import(positions, erreur)

    @mainthread
    def _traiter_import(self, positions, erreur):
        self.import_en_cours = False
        if erreur:
            self.import_statut_txt = f"Échec : {erreur}"
            self.import_statut_color = list(RED)
            return
        if not positions:
            self.import_statut_txt = "Aucune position trouvée sur Trading212."
            self.import_statut_color = list(ORANGE)
            return

        existantes = {p["ticker"].upper() for p in storage.charger_positions()}
        ajoutees = 0
        non_resolus = []
        for p in positions:
            ticker = p.get("ticker", "").strip().upper()
            if not ticker or ticker in existantes:
                continue
            try:
                storage.ajouter_position(ticker, p["quantite"], p["pru"],
                                          date_achat=datetime.now().strftime("%Y-%m-%d"))
                ajoutees += 1
                existantes.add(ticker)
                # Si le ticker importé est identique au ticker T212 brut,
                # c'est que la conversion (générique + ISIN) a échoué.
                if ticker == p.get("ticker_t212_origine", "").upper():
                    non_resolus.append(ticker)
            except Exception:
                continue

        message = f"{ajoutees} position(s) importée(s) sur {len(positions)} trouvée(s)."
        if non_resolus:
            message += (f" ! {len(non_resolus)} ticker(s) non résolu(s), à corriger "
                        f"manuellement : {', '.join(non_resolus)}")
            self.import_statut_color = list(ORANGE)
        else:
            self.import_statut_color = list(GREEN)
        self.import_statut_txt = message


class DetailScreen(Screen):
    nom = StringProperty("")
    ticker = StringProperty("")
    resume_txt = StringProperty("")
    notes_sante = ListProperty([])
    notes_div = ListProperty([])
    notes_sante_txt = StringProperty("")
    notes_div_txt = StringProperty("")
    technique_txt = StringProperty("")
    analystes_txt = StringProperty("")
    alertes_txt = StringProperty("")
    quantite_detail_txt = StringProperty("")
    prix_actuel_txt = StringProperty("")
    pv_mv_detail_txt = StringProperty("")
    pv_mv_detail_color = ListProperty(list(TXT_MUTED))
    note_sante_txt = StringProperty("N/A")
    note_div_txt = StringProperty("N/A")
    verdict_txt = StringProperty("")
    verdict_color = ListProperty(list(TXT_MUTED))
    verdict_bg = ListProperty([0, 0, 0, 0])
    _resultat = None

    def _peupler_tuiles_notes(self, box, notes):
        box.clear_widgets()
        liste = notes or ["Données insuffisantes."]
        for n in liste:
            tuile = Factory.NoteTile()
            tuile.texte = f"• {n}"
            box.add_widget(tuile)

    def charger(self, resultat):
        self._resultat = resultat
        self.nom = resultat.get("nom") or resultat.get("ticker", "")
        self.ticker = resultat.get("ticker", "")
        r = resultat

        lignes = [f"[b]{r.get('nom')}[/b] ({r.get('ticker')})", ""]
        if r.get("erreur"):
            lignes.append(f"[color=e04c4c]Erreur: {r['erreur']}[/color]")
        else:
            positions_locales = storage.charger_positions()
            quantite_detenue = None
            for p in positions_locales:
                if p["ticker"].upper() == r.get("ticker", "").upper():
                    quantite_detenue = p.get("quantite")
                    break
            if quantite_detenue is not None:
                lignes.append(f"Quantité détenue : {quantite_detenue:g} actions")

            prix = r.get("prix_actuel")
            devise = r.get("devise") or ""
            lignes.append(f"Prix actuel : {prix:.2f} {devise}" if prix is not None else "Prix actuel : N/A")
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
                lignes.append(f"Dividende annuel estimé : {r['prochain_dividende_montant']:.2f} / action")
            if r.get("rendement_pct") is not None:
                lignes.append(f"Rendement actuel : {r['rendement_pct']:.2f}%")
            lignes.append("")
            note_s = r.get("note_sante")
            note_d = r.get("note_div")
            lignes.append(f"Note santé financière : {note_s}/10" if note_s is not None else "Note santé financière : N/A")
            lignes.append(f"Note fiabilité dividende : {note_d}/10" if note_d is not None else "Note fiabilité dividende : N/A")
            lignes.append(f"Verdict : {r.get('verdict', '')}")

        self.resume_txt = "\n".join(lignes)
        self.notes_sante = r.get("notes_sante", [])
        self.notes_div = r.get("notes_div", [])
        self.notes_sante_txt = "\n".join(f"• {n}" for n in self.notes_sante) or "Données insuffisantes."
        self.notes_div_txt = "\n".join(f"• {n}" for n in self.notes_div) or "Données insuffisantes."
        self._peupler_tuiles_notes(self.ids.notes_sante_box, self.notes_sante)
        self._peupler_tuiles_notes(self.ids.notes_div_box, self.notes_div)

        # --- Cartes du haut (quantité / prix / PV-MV / scores / verdict) ---
        quantite_detenue = None
        for p in storage.charger_positions():
            if p["ticker"].upper() == r.get("ticker", "").upper():
                quantite_detenue = p.get("quantite")
                break
        self.quantite_detail_txt = f"{quantite_detenue:g} actions" if quantite_detenue is not None else "—"

        prix = r.get("prix_actuel")
        devise = r.get("devise") or ""
        self.prix_actuel_txt = f"{prix:.2f} {devise}".strip() if prix is not None else "N/A"

        pv = r.get("pv_mv_eur")
        if pv is not None:
            signe = "+" if pv >= 0 else ""
            pct = r.get("pv_mv_pct")
            pct_txt = f"({signe}{pct:.1f}%)" if pct is not None else ""
            # Saut de ligne explicite (comme pour les tuiles du portefeuille) :
            # évite qu'un retour à la ligne automatique ne fasse déborder le
            # montant hors de la zone visible.
            self.pv_mv_detail_txt = f"{signe}{pv:.2f} €" + (f"\n{pct_txt}" if pct_txt else "")
            self.pv_mv_detail_color = list(couleur_pv(pv))
        else:
            self.pv_mv_detail_txt = "N/A"
            self.pv_mv_detail_color = list(TXT_MUTED)

        self.note_sante_txt = f"{r['note_sante']}" if r.get("note_sante") is not None else "N/A"
        self.note_div_txt = f"{r['note_div']}" if r.get("note_div") is not None else "N/A"

        verdict = r.get("verdict", "")
        self.verdict_txt = verdict.split(" ", 1)[-1] if " " in verdict else verdict
        if verdict.startswith("OK"):
            self.verdict_color = [0.322, 0.780, 0.478, 1]
            self.verdict_bg = [0.322, 0.780, 0.478, 0.12]
        elif verdict.startswith("KO"):
            self.verdict_color = [0.918, 0.380, 0.380, 1]
            self.verdict_bg = [0.918, 0.380, 0.380, 0.12]
        elif verdict.startswith("MOYEN"):
            self.verdict_color = [0.949, 0.651, 0.247, 1]
            self.verdict_bg = [0.949, 0.651, 0.247, 0.12]
        else:
            self.verdict_color = list(TXT_MUTED)
            self.verdict_bg = [0.482, 0.51, 0.564, 0.12]

        # --- Analyse technique (SMA50/SMA200, volume) ---
        lignes_tech = []
        sma50 = r.get("sma50")
        sma200 = r.get("sma200")
        if sma50 is not None:
            lignes_tech.append(f"Moyenne mobile 50j : {sma50:.2f}")
        if sma200 is not None:
            lignes_tech.append(f"Moyenne mobile 200j : {sma200:.2f}")
        au_dessus = r.get("au_dessus_sma200")
        if au_dessus is not None:
            couleur = "5ecc66" if au_dessus else "e04c4c"
            position = "au-dessus" if au_dessus else "en-dessous"
            lignes_tech.append(f"[color={couleur}]Cours actuellement {position} de sa SMA200[/color]")
        croisement = r.get("sma50_au_dessus_sma200")
        if croisement is not None:
            couleur = "5ecc66" if croisement else "e04c4c"
            etat = "SMA50 au-dessus de la SMA200 (config. haussière)" if croisement else "SMA50 en-dessous de la SMA200 (config. baissière)"
            lignes_tech.append(f"[color={couleur}]{etat}[/color]")
        ratio_vol = r.get("ratio_volume")
        if ratio_vol is not None:
            lignes_tech.append(f"Volume vs moyenne : x{ratio_vol:.1f}")
        self.technique_txt = "\n".join(lignes_tech)

        # --- Alertes actives ---
        alertes = r.get("alertes", [])
        if alertes:
            lignes_alertes = ["[b][color=f2a63f]! ALERTES ACTIVES[/color][/b]"]
            for a in alertes:
                lignes_alertes.append(f"[color=f2a63f]• {a.get('message', '')}[/color]")
            self.alertes_txt = "\n".join(lignes_alertes)
        else:
            self.alertes_txt = ""

    def ouvrir_actualites(self):
        news_screen = self.manager.get_screen("news")
        news_screen.charger(self.ticker, self.nom)
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "news"

    def ouvrir_analystes(self):
        analystes_screen = self.manager.get_screen("analystes")
        analystes_screen.charger(self.ticker, self.nom, self._resultat.get("devise") if self._resultat else "")
        self.manager.transition = SlideTransition(direction="left")
        self.manager.current = "analystes"

    def supprimer(self):
        if self._resultat:
            positions = storage.charger_positions()
            ticker = self._resultat.get("ticker")
            for i, p in enumerate(positions):
                if p["ticker"] == ticker:
                    storage.supprimer_position(i)
                    break
        self.manager.current = "portfolio"


class NewsScreen(Screen):
    nom = StringProperty("")
    statut_txt = StringProperty("")
    _ticker = None

    def charger(self, ticker, nom):
        self._ticker = ticker
        self.nom = nom
        self.ids.news_box.clear_widgets()
        self.statut_txt = "Chargement des actualités..."
        threading.Thread(target=self._charger_en_fond, args=(ticker,), daemon=True).start()

    def _charger_en_fond(self, ticker):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        actus = api_client.obtenir_actualites(server_url, ticker)
        self._afficher(actus)

    @mainthread
    def _afficher(self, actus):
        self.ids.news_box.clear_widgets()
        if not actus:
            self.statut_txt = "Aucune actualité trouvée (ou serveur injoignable)."
            return
        self.statut_txt = ""
        for item in actus:
            row = Factory.NewsRow()
            row.titre = item.get("titre", "")
            row.editeur = item.get("editeur", "")
            row.date_txt = item.get("date", "")
            row.sentiment = item.get("sentiment", "neutre")
            row.lien = item.get("lien", "")
            row.alerte = bool(item.get("alerte", False))
            row.source = item.get("source", "")
            if row.lien:
                row.bind(on_touch_up=lambda inst, touch, url=row.lien:
                          webbrowser.open(url) if inst.collide_point(*touch.pos) else None)
            self.ids.news_box.add_widget(row)


class AnalystesScreen(Screen):
    nom = StringProperty("")
    statut_txt = StringProperty("")
    contenu_txt = StringProperty("")
    source_txt = StringProperty("")
    _ticker = None
    _devise = ""

    def charger(self, ticker, nom, devise=""):
        self._ticker = ticker
        self._devise = devise or ""
        self.nom = nom
        self.contenu_txt = ""
        self.statut_txt = "Chargement des avis analystes..."
        threading.Thread(target=self._charger_en_fond, args=(ticker,), daemon=True).start()

    def _charger_en_fond(self, ticker):
        settings = storage.charger_settings()
        server_url = settings.get("server_url", "")
        avis = api_client.obtenir_avis_analystes(server_url, ticker)
        self._afficher(avis)

    @mainthread
    def _afficher(self, avis):
        if not avis:
            self.statut_txt = "Aucun avis analyste disponible pour ce titre."
            self.contenu_txt = ""
            self.source_txt = ""
            return

        self.statut_txt = ""
        lignes = []

        # Cas Finnhub : répartition détaillée des recommandations
        if avis.get("strong_buy") is not None:
            total = avis.get("nb_analystes") or 0
            lignes.append(f"[b]{total} analyste(s)[/b]" + (f" — période {avis['periode']}" if avis.get("periode") else ""))
            lignes.append("")
            lignes.append(f"[color=4caf50]Achat fort[/color] : {avis.get('strong_buy', 0)}")
            lignes.append(f"[color=4caf50]Achat[/color] : {avis.get('buy', 0)}")
            lignes.append(f"[color=9fa3ab]Conserver[/color] : {avis.get('hold', 0)}")
            lignes.append(f"[color=e05555]Vente[/color] : {avis.get('sell', 0)}")
            lignes.append(f"[color=e05555]Vente forte[/color] : {avis.get('strong_sell', 0)}")
        # Cas repli yfinance : juste un consensus global
        elif avis.get("consensus"):
            lignes.append(f"Consensus : [b]{avis['consensus']}[/b]"
                           + (f" ({avis['nb_analystes']} analystes)" if avis.get("nb_analystes") else ""))

        prix_cible = avis.get("prix_cible_moyen")
        if prix_cible:
            lignes.append("")
            ligne_cible = f"Objectif de cours moyen : [b]{prix_cible:.2f} {self._devise}[/b]"
            if avis.get("prix_cible_bas") and avis.get("prix_cible_haut"):
                ligne_cible += f"\n(entre {avis['prix_cible_bas']:.2f} et {avis['prix_cible_haut']:.2f})"
            lignes.append(ligne_cible)

        self.contenu_txt = "\n".join(lignes) if lignes else "Données incomplètes."
        self.source_txt = f"Source : {avis.get('source', 'inconnue')}"


class SettingsScreen(Screen):
    statut_txt = StringProperty("")
    statut_color = ListProperty(list(WHITE))

    def on_pre_enter(self):
        settings = storage.charger_settings()
        self.ids.url_input.text = settings.get("server_url", "")
        self.statut_txt = ""

    def tester(self):
        url = self.ids.url_input.text.strip()
        self.statut_txt = "Test en cours..."
        self.statut_color = list(WHITE)
        threading.Thread(target=self._tester_en_fond, args=(url,), daemon=True).start()

    def _tester_en_fond(self, url):
        ok, message = api_client.tester_connexion(url)
        self._afficher_statut(ok, message)

    @mainthread
    def _afficher_statut(self, ok, message):
        self.statut_txt = message
        self.statut_color = list(GREEN) if ok else list(RED)

    def enregistrer(self):
        url = self.ids.url_input.text.strip()
        if url:
            storage.set_server_url(url)
            self.statut_txt = "Enregistré."
            self.statut_color = list(GREEN)


class SuiviBourseApp(App):
    # Écran parent de chaque écran, pour que le bouton retour Android
    # remonte dans la hiérarchie de navigation au lieu de fermer l'app.
    # Un écran absent de cette table (ex: "portfolio", l'écran racine)
    # laisse le comportement par défaut d'Android s'exécuter (fermer l'app).
    _ECRAN_PARENT = {
        "add": "portfolio",
        "settings": "portfolio",
        "detail": "portfolio",
        "news": "detail",
        "analystes": "detail",
    }

    def build(self):
        Builder.load_string(KV)
        sm = ScreenManager()
        sm.add_widget(PortfolioScreen())
        sm.add_widget(AddPositionScreen())
        sm.add_widget(DetailScreen())
        sm.add_widget(NewsScreen())
        sm.add_widget(AnalystesScreen())
        sm.add_widget(SettingsScreen())
        Window.bind(on_keyboard=self._on_keyboard)
        return sm

    def _on_keyboard(self, window, key, *args):
        # keycode 27 = touche "retour" Android (mappée sur Escape par Kivy)
        if key == 27:
            ecran_actuel = self.root.current
            ecran_parent = self._ECRAN_PARENT.get(ecran_actuel)
            if ecran_parent:
                self.root.transition = SlideTransition(direction="right")
                self.root.current = ecran_parent
                return True  # événement consommé : on ne ferme pas l'app
            return False  # sur l'écran racine : comportement Android normal (quitter)
        return False


if __name__ == "__main__":
    SuiviBourseApp().run()
