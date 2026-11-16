"""Interface graphique discrète pour le solveur QCM (qcm.py)."""

import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import qcm

BG = "#121318"
CARD_BG = "#1c1e26"
BTN_BG = "#262934"
BTN_ACTIVE = "#323645"
BORDER = "#2d3140"
FG = "#f8fafc"
MUTED = "#94a3b8"
GREEN = "#22c55e"
AMBER = "#f59e0b"
RED = "#ef4444"


class QcmApp:
    """Mini fenêtre flottante sombre avec surveillance presse-papiers FIFO."""

    def __init__(
        self,
        root: tk.Tk,
        model: str | None = None,
        lire_fn=None,
        seq_fn=None,
        lire_image_fn=None,
    ) -> None:
        self.root = root
        cfg = qcm.charger_config()
        self.model = model or cfg["model"]
        self.catalogue: dict[str, dict[str, str]] = {
            k: dict(v) for k, v in qcm.MODELES_DEFAUT.items()
        }
        famille_init, effort_init = qcm.trouver_famille_et_effort(self.catalogue, self.model)
        self.famille_courante = famille_init
        self.effort = effort_init if model is None else effort_init

        self._utilise_clipboard_systeme = lire_fn is None
        self.lire_fn = lire_fn or qcm.lire_presse_papiers
        self.lire_image_fn = lire_image_fn if lire_image_fn is not None else (
            qcm.lire_image_presse_papiers if self._utilise_clipboard_systeme else (lambda: None)
        )
        self.seq_fn = seq_fn if seq_fn is not None else (
            qcm.numero_sequence_presse_papiers if self._utilise_clipboard_systeme else None
        )
        self.en_pause = False
        self.toujours_devant = True
        self.compteur = 0
        self.en_cours = False
        self.derniere_duree: float | None = None
        self.dernier_texte = (self.lire_fn() or "").strip()
        self.dernier_seq = self.seq_fn() if self.seq_fn else None

        self.task_queue: queue.Queue[tuple[str, int, str]] = queue.Queue()
        self.ui_queue: queue.Queue[tuple] = queue.Queue()
        self.stop_event = threading.Event()

        self._configurer_fenetre()
        self._construire_interface()

        self.worker = threading.Thread(target=self._boucle_travailleur, daemon=True)
        self.worker.start()
        threading.Thread(target=self._charger_modeles_async, daemon=True).start()

        self.root.after(150, self._verifier_presse_papiers)
        self.root.after(80, self._traiter_retours_ui)

    def _configurer_fenetre(self) -> None:
        self.root.title("QCM")
        self.root.geometry("410x285")
        self.root.minsize(330, 190)
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.fermer)

    def _configurer_style_combos(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Dark.TCombobox",
            fieldbackground=CARD_BG,
            background=BTN_BG,
            foreground=FG,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            selectbackground=BTN_ACTIVE,
            selectforeground=FG,
            padding=2,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", CARD_BG)],
            foreground=[("readonly", FG)],
            background=[("readonly", BTN_BG)],
        )
        self.root.option_add("*TCombobox*Listbox.background", CARD_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", BTN_ACTIVE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", FG)

    def _construire_interface(self) -> None:
        self._configurer_style_combos()

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=10, pady=(8, 4))

        self.dot = tk.Label(header, text="●", fg=GREEN, bg=BG, font=("Segoe UI", 10))
        self.dot.pack(side="left")

        self.status_var = tk.StringVar(value="Prêt — copie une question")
        self.status_lbl = tk.Label(
            header,
            textvariable=self.status_var,
            fg=MUTED,
            bg=BG,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.status_lbl.pack(side="left", padx=(6, 4), fill="x", expand=True)

        self.btn_compte = self._creer_bouton(header, "Compte", self.ouvrir_compte)
        self.btn_compte.pack(side="right", padx=(4, 0))

        self.btn_clear = self._creer_bouton(header, "Vider", self.vider)
        self.btn_clear.pack(side="right", padx=(4, 0))

        self.btn_pause = self._creer_bouton(header, "Pause", self.basculer_pause)
        self.btn_pause.pack(side="right", padx=(4, 0))

        self.btn_lire = self._creer_bouton(header, "Lire", self.analyser_maintenant)
        self.btn_lire.pack(side="right", padx=(4, 0))

        self.btn_top = self._creer_bouton(header, "Top", self.basculer_devant, actif=True)
        self.btn_top.pack(side="right")

        # Barre Modèle + Effort
        barre_modele = tk.Frame(self.root, bg=BG)
        barre_modele.pack(fill="x", padx=10, pady=(0, 6))

        self.famille_var = tk.StringVar(value=self.famille_courante)
        self.combo_modele = ttk.Combobox(
            barre_modele,
            textvariable=self.famille_var,
            values=list(self.catalogue.keys()),
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 8),
            width=22,
        )
        self.combo_modele.pack(side="left", fill="x", expand=True)
        self.combo_modele.bind("<<ComboboxSelected>>", self._sur_changement_famille)

        self.effort_var = tk.StringVar(value=self.effort)
        efforts_init = list(self.catalogue[self.famille_courante].keys())
        self.combo_effort = ttk.Combobox(
            barre_modele,
            textvariable=self.effort_var,
            values=efforts_init,
            state="readonly",
            style="Dark.TCombobox",
            font=("Segoe UI", 8),
            width=9,
        )
        self.combo_effort.pack(side="left", padx=(6, 0))
        self.combo_effort.bind("<<ComboboxSelected>>", self._sur_changement_effort)

        self.root.bind("<F5>", lambda _e: self.analyser_maintenant())
        self.root.bind("<Return>", lambda _e: self.analyser_maintenant())

        # Carte réponse principale
        card = tk.Frame(
            self.root,
            bg=CARD_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=12,
            pady=10,
        )
        card.pack(fill="x", padx=10, pady=(0, 8))

        self.font_grand = tkfont.Font(family="Segoe UI", size=18, weight="bold")
        self.font_moyen = tkfont.Font(family="Segoe UI", size=12, weight="bold")
        self.font_petit = tkfont.Font(family="Segoe UI", size=10)

        msg_initial = (
            "Installe agy puis clique sur Compte"
            if not qcm._trouver_agy()
            else "En attente d'un Ctrl+C ou Win+Shift+S…"
        )
        self.reponse_var = tk.StringVar(value=msg_initial)
        self.reponse_lbl = tk.Label(
            card,
            textvariable=self.reponse_var,
            fg=MUTED,
            bg=CARD_BG,
            font=self.font_petit,
            justify="left",
            anchor="w",
            wraplength=305,
        )
        self.reponse_lbl.pack(fill="x")
        card.bind("<Configure>", lambda e: self.reponse_lbl.configure(wraplength=max(180, e.width - 24)))

        # Historique discret FIFO
        hist_frame = tk.Frame(self.root, bg=BG)
        hist_frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.historique = tk.Text(
            hist_frame,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            wrap="word",
            state="disabled",
            cursor="arrow",
        )
        self.historique.tag_configure("num", foreground="#64748b")
        self.historique.tag_configure("rep", foreground=FG)
        self.historique.pack(fill="both", expand=True)

    def _creer_bouton(self, parent: tk.Widget, texte: str, commande, actif: bool = False) -> tk.Button:
        return tk.Button(
            parent,
            text=texte,
            command=commande,
            bg=BTN_BG,
            fg=GREEN if actif else MUTED,
            activebackground=BTN_ACTIVE,
            activeforeground=FG,
            relief="flat",
            borderwidth=0,
            padx=7,
            pady=2,
            font=("Segoe UI", 8, "bold" if actif else "normal"),
            cursor="hand2",
        )

    def basculer_devant(self) -> None:
        self.toujours_devant = not self.toujours_devant
        self.root.attributes("-topmost", self.toujours_devant)
        self.btn_top.configure(
            fg=GREEN if self.toujours_devant else MUTED,
            font=("Segoe UI", 8, "bold" if self.toujours_devant else "normal"),
        )

    def basculer_pause(self) -> None:
        self.en_pause = not self.en_pause
        self.btn_pause.configure(
            text="Repr." if self.en_pause else "Pause",
            fg=AMBER if self.en_pause else MUTED,
        )
        self._maj_statut()

    def vider(self) -> None:
        self.compteur = 0
        self.derniere_duree = None
        self.reponse_var.set("En attente d'un Ctrl+C ou Win+Shift+S…")
        self.reponse_lbl.configure(fg=MUTED, font=self.font_petit)
        self.historique.configure(state="normal")
        self.historique.delete("1.0", "end")
        self.historique.configure(state="disabled")
        self._maj_statut()

    def _maj_statut(self) -> None:
        en_attente = self.task_queue.qsize()
        if self.en_cours or en_attente > 0:
            extra = max(0, en_attente - (0 if self.en_cours else 1))
            suffix = f" (+{extra} en file)" if extra else ""
            self.dot.configure(fg=AMBER)
            self.status_var.set(f"Réflexion…{suffix}")
        elif self.en_pause:
            self.dot.configure(fg=AMBER)
            self.status_var.set("En pause")
        else:
            self.dot.configure(fg=GREEN)
            if self.derniere_duree is not None:
                self.status_var.set(f"Prêt ({self.derniere_duree:.2f}s)")
            else:
                self.status_var.set("Prêt — Ctrl+C ou Win+Shift+S")

    def _lire_texte_securise(self) -> str | None:
        actuel = self.lire_fn()
        if actuel is None and self._utilise_clipboard_systeme:
            try:
                actuel = self.root.clipboard_get()
            except tk.TclError:
                return None
        return actuel

    def _enfiler_question(self, propre: str) -> None:
        self.dernier_texte = propre
        self.compteur += 1
        self.task_queue.put(("text", self.compteur, propre))
        self._maj_statut()

    def _enfiler_image(self, chemin_image: str) -> None:
        self.compteur += 1
        self.task_queue.put(("image", self.compteur, chemin_image))
        self._maj_statut()

    def _traiter_presse_papiers_courant(self) -> bool:
        """Lit le texte ou l'image du presse-papiers et l'enfile. Retourne False si verrouillé."""
        actuel = self._lire_texte_securise()
        if actuel is None:
            return False
        propre = actuel.strip()
        if propre and len(propre) >= qcm.MIN_TEXT_LENGTH:
            self._enfiler_question(propre)
            return True
        img_path = self.lire_image_fn()
        if img_path:
            self._enfiler_image(img_path)
            return True
        return True

    def analyser_maintenant(self) -> None:
        if self.seq_fn:
            self.dernier_seq = self.seq_fn()
        self._traiter_presse_papiers_courant()

    def _verifier_presse_papiers(self) -> None:
        if self.stop_event.is_set():
            return
        if not self.en_pause:
            if self.seq_fn is not None:
                seq = self.seq_fn()
                if seq != self.dernier_seq:
                    if self._traiter_presse_papiers_courant():
                        self.dernier_seq = seq
            else:
                actuel = self._lire_texte_securise()
                if actuel is not None:
                    propre = actuel.strip()
                    if propre and len(propre) >= qcm.MIN_TEXT_LENGTH and propre != self.dernier_texte:
                        self._enfiler_question(propre)
        self.root.after(150, self._verifier_presse_papiers)

    def _charger_modeles_async(self) -> None:
        catalogue = qcm.lister_modeles_agy()
        if not self.stop_event.is_set():
            self.ui_queue.put(("catalogue", catalogue))

    def _appliquer_catalogue(self, catalogue: dict[str, dict[str, str]]) -> None:
        self.catalogue = catalogue
        self.combo_modele.configure(values=list(catalogue.keys()))
        famille, effort = qcm.trouver_famille_et_effort(catalogue, self.model)
        self.famille_courante = famille
        self.effort = effort
        self.famille_var.set(famille)
        efforts = list(catalogue[famille].keys())
        self.combo_effort.configure(values=efforts)
        self.effort_var.set(effort)
        self.model = catalogue[famille][effort]

    def _sur_changement_famille(self, _event=None) -> None:
        famille = self.famille_var.get()
        if famille not in self.catalogue:
            return
        self.famille_courante = famille
        efforts = list(self.catalogue[famille].keys())
        self.combo_effort.configure(values=efforts)
        if self.effort not in self.catalogue[famille]:
            self.effort = "Medium" if "Medium" in self.catalogue[famille] else efforts[0]
        self.effort_var.set(self.effort)
        self.model = self.catalogue[famille][self.effort]
        qcm.sauvegarder_config(self.model, self.effort)

    def _sur_changement_effort(self, _event=None) -> None:
        effort = self.effort_var.get()
        efforts_map = self.catalogue.get(self.famille_courante, {})
        if effort not in efforts_map:
            return
        self.effort = effort
        self.model = efforts_map[effort]
        qcm.sauvegarder_config(self.model, self.effort)

    def ouvrir_compte(self) -> None:
        ok, message = qcm.ouvrir_connexion_agy()
        self.reponse_lbl.configure(fg=GREEN if ok else RED, font=self.font_petit)
        self.reponse_var.set(message)

    def _boucle_travailleur(self) -> None:
        while not self.stop_event.is_set():
            try:
                kind, idx, contenu = self.task_queue.get(timeout=0.15)
            except queue.Empty:
                continue
            try:
                modele_actif = self.model
                effort_actif = self.effort
                self.ui_queue.put(("debut", idx))
                debut = time.monotonic()
                try:
                    if kind == "image":
                        reponse = qcm.interroger_agy_image(contenu, model=modele_actif, effort=effort_actif)
                    else:
                        reponse = qcm.interroger_agy(contenu, model=modele_actif, effort=effort_actif)
                except Exception as exc:
                    reponse = f"[erreur] {exc}"
                duree = time.monotonic() - debut
                self.ui_queue.put(("fin", idx, reponse, duree))
            finally:
                self.task_queue.task_done()

    def _traiter_retours_ui(self) -> None:
        if self.stop_event.is_set():
            return
        while True:
            try:
                msg = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if msg[0] == "debut":
                self.en_cours = True
                self._maj_statut()
            elif msg[0] == "fin":
                _, idx, reponse, duree = msg
                self.en_cours = False
                self.derniere_duree = duree
                self._afficher_reponse(idx, reponse, duree)
                self._maj_statut()
            elif msg[0] == "catalogue":
                self._appliquer_catalogue(msg[1])

        self.root.after(80, self._traiter_retours_ui)

    def _afficher_reponse(self, idx: int, reponse: str, duree: float) -> None:
        est_erreur = reponse.startswith("[erreur")
        est_multi = "\n" in reponse and not est_erreur

        if est_erreur:
            self.reponse_lbl.configure(fg=RED, font=self.font_petit)
            self.reponse_var.set(reponse)
        elif est_multi:
            lignes = [l for l in reponse.splitlines() if l.strip()]
            self.reponse_lbl.configure(fg=GREEN, font=self.font_moyen)
            self.reponse_var.set(f"{len(lignes)} réponses prêtes ({duree:.2f}s)")
        else:
            police = self.font_grand if len(reponse) <= 4 else self.font_moyen
            self.reponse_lbl.configure(fg=FG, font=police)
            self.reponse_var.set(reponse)

        self.historique.configure(state="normal")
        if est_multi:
            debut_bloc = self.historique.index("end-1c")
            lignes = [l for l in reponse.splitlines() if l.strip()]
            self.historique.insert("end", f"— {len(lignes)} réponses ({duree:.2f}s) —\n", "num")
            for ligne in lignes:
                if ":" in ligne:
                    num, rep = ligne.split(":", 1)
                    self.historique.insert("end", f"{num.strip()}: ", "num")
                    self.historique.insert("end", f"{rep.strip()}\n", "rep")
                else:
                    self.historique.insert("end", f"{ligne}\n", "rep")
            self.historique.see(debut_bloc)
        else:
            self.historique.insert("end", f"#{idx}  ", "num")
            self.historique.insert("end", f"{reponse}", "rep")
            self.historique.insert("end", f"  ({duree:.2f}s)\n", "num")
            self.historique.see("end")
        self.historique.configure(state="disabled")

    def fermer(self) -> None:
        self.stop_event.set()
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    QcmApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
