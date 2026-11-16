"""Mini solveur QCM discret via Antigravity CLI (agy).

Utilisation :
  python qcm.py            -> surveille le presse-papiers automatiquement (Ctrl+C sur une question)
  python qcm.py --manual   -> attend que tu appuies sur Entrée après avoir copié
  python qcm.py --once     -> résout le presse-papiers actuel une seule fois et quitte
  python qcm.py "Question" -> résout la question passée en argument et quitte
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

MODEL = "gemini-4-hydrogen-medium"
EFFORT_DEFAUT = "Medium"
POLL_INTERVAL = 0.15
MIN_TEXT_LENGTH = 6
TIMEOUT_SECONDS = 45
CONFIG_PATH = Path.home() / ".qcm_agy_config.json"
EFFORTS_ORDRE = ("Low", "Medium", "High", "XHigh", "Max", "Auto")
_EFFORT_SUFFIXES = ("-low", "-medium", "-high", "-xhigh", "-max")

MODELES_DEFAUT: dict[str, dict[str, str]] = {
    "Gemini 4 Hydrogen": {
        "Low": "gemini-4-hydrogen-low",
        "Medium": "gemini-4-hydrogen-medium",
        "High": "gemini-4-hydrogen-high",
        "XHigh": "gemini-4-hydrogen-xhigh",
    },
    "Gemini 4 Argon": {
        "Low": "gemini-4-argon-low",
        "Medium": "gemini-4-argon-medium",
        "High": "gemini-4-argon-high",
    },
    "Gemini 3.8 Flash": {
        "Low": "gemini-3.8-flash-low",
        "Medium": "gemini-3.8-flash-medium",
        "High": "gemini-3.8-flash-high",
    },
    "Claude Sonnet 5.5": {
        "Low": "claude-sonnet-5-5-low",
        "Medium": "claude-sonnet-5-5-medium",
        "High": "claude-sonnet-5-5-high",
    },
    "Claude Opus 5.5": {
        "Low": "claude-opus-5-5-low",
        "Medium": "claude-opus-5-5-medium",
        "High": "claude-opus-5-5-high",
    },
    "GPT-OSS 120B": {
        "Medium": "gpt-oss-120b-medium",
    },
}

SYSTEM_PHRASE = (
    "Tu es un moteur QCM silencieux. Ne fais aucun appel d'outil et n'explique rien. "
    "Réponds UNIQUEMENT par la phrase exacte de la bonne réponse, "
    "sans lettre d'option (pas de A), B), C., etc.), sans préfixe, sans guillemets ni Markdown."
)

SYSTEM_LETTRE = (
    "Tu es un moteur QCM silencieux. Ne fais aucun appel d'outil et n'explique rien. "
    "Les choix sont identifiés par des lettres. "
    "Réponds UNIQUEMENT par la lettre majuscule de la bonne réponse (ex: A, B, C ou D), "
    "sans parenthèse, sans point, sans texte, sans guillemets ni Markdown."
)

SYSTEM_MULTI = (
    "Tu es un moteur QCM silencieux. Ne fais aucun appel d'outil et n'explique rien. "
    "Le texte contient plusieurs questions numérotées. "
    "Pour CHAQUE question, réponds sur une ligne au format exact `NUMERO: REPONSE` "
    "(où REPONSE est uniquement la lettre majuscule si des options a/b/c/d sont présentes, "
    "sinon la phrase exacte de la bonne réponse). Aucune autre ligne, aucun préfixe, aucun Markdown."
)

SYSTEM_IMAGE = (
    "Tu es un moteur QCM silencieux. Utilise uniquement ton outil de lecture pour lire le fichier image indiqué, "
    "repère la ou les questions visibles (ignore les menus/barres autour), puis n'explique rien.\n"
    "- S'il y a UNE seule question : si les choix ont des lettres (A/B/C/D, a/b/c/d), "
    "réponds UNIQUEMENT par la lettre majuscule ; sinon, réponds UNIQUEMENT par la phrase exacte de la bonne réponse.\n"
    "- S'il y a PLUSIEURS questions numérotées, réponds sur des lignes au format exact `NUMERO: REPONSE`.\n"
    "Aucun préfixe, aucune explication, aucun Markdown."
)

# --- Lecture du presse-papiers Windows sans dépendance externe ---
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CF_DIB = 8
_CF_UNICODETEXT = 13

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.GetClipboardSequenceNumber.argtypes = []
_user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL


def numero_sequence_presse_papiers() -> int:
    """Retourne le compteur Windows incrémenté à chaque copie (Ctrl+C)."""
    return int(_user32.GetClipboardSequenceNumber())


def lire_presse_papiers() -> str | None:
    """Retourne le texte du presse-papiers Windows, ou None s'il est temporairement verrouillé."""
    if not _user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
        return ""
    if not _user32.OpenClipboard(None):
        return None
    try:
        handle = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def vider_presse_papiers() -> bool:
    """Vide le presse-papiers Windows."""
    if not _user32.OpenClipboard(None):
        return False
    try:
        return bool(_user32.EmptyClipboard())
    finally:
        _user32.CloseClipboard()


def _sauvegarder_image_temp(img, prefixe: str = "clip") -> str:
    """Enregistre une image PIL en PNG temporaire et retourne son chemin."""
    dossier = Path(tempfile.gettempdir()) / "qcm_clip_imgs"
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / f"{prefixe}_{time.time_ns()}.png"
    img.save(chemin, format="PNG")
    return str(chemin)


def lire_image_presse_papiers(vider_apres: bool = True) -> str | None:
    """Sauvegarde l'image du presse-papiers en PNG temporaire, puis vide le presse-papiers."""
    if not _user32.IsClipboardFormatAvailable(_CF_DIB):
        return None
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    try:
        img = ImageGrab.grabclipboard()
    except OSError:
        return None
    if img is None or not hasattr(img, "save"):
        return None

    chemin = _sauvegarder_image_temp(img, prefixe="clip")
    if vider_apres:
        vider_presse_papiers()
    return chemin


def capturer_ecran() -> str | None:
    """Capture tout l'écran en PNG temporaire et retourne son chemin (ou None si impossible)."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    try:
        img = ImageGrab.grab(all_screens=True)
    except OSError:
        try:
            img = ImageGrab.grab()
        except OSError:
            return None
    return _sauvegarder_image_temp(img, prefixe="screen")


def detecter_lettres(question: str) -> bool:
    """Détecte si la question contient des choix préfixés par des lettres (A, B, C...)."""
    lettres = set()
    lettres.update(m.upper() for m in re.findall(r"(?:^|\s)[\(\[]?([A-Ha-h])[\)\.\]](?=\s+\S)", question))
    lettres.update(m.upper() for m in re.findall(r"(?m)^\s*([A-Ha-h])\s*[-:](?=\s+\S)", question))
    lettres.update(m.upper() for m in re.findall(r"(?m)^\s*([A-Ha-h])[\)\.]?\s*$", question))
    return len(lettres) >= 2 and "A" in lettres and "B" in lettres


def extraire_numeros_questions(texte: str) -> list[str]:
    """Retourne la liste ordonnée des numéros si le texte contient >= 2 questions."""
    vus: set[str] = set()
    numeros: list[str] = []
    for num in re.findall(r"(?im)^[ \t]*(?:Question\s+|Q\s*)(\d+)\b(?!\s*R[ée]ponse)[^\r\n]*", texte):
        if num not in vus:
            vus.add(num)
            numeros.append(num)
    return numeros if len(numeros) >= 2 else []


def nettoyer_reponse(texte: str, lettre_seule: bool = False) -> str:
    """Supprime balises, préfixes d'options éventuels et formatage parasite."""
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.DOTALL)
    texte = texte.strip().strip('"\'`«»')
    texte = re.sub(r"^\*\*(.*)\*\*$", r"\1", texte).strip()
    texte = re.sub(r"^(?:Réponse|Reponse|Answer)\s*:\s*", "", texte, flags=re.IGNORECASE)
    if lettre_seule:
        match = re.match(r"^[\(\[]?([A-Ha-h])[\)\]]?(?:[\.\:\-\s]|$)", texte)
        if match:
            return match.group(1).upper()
        return " ".join(texte.splitlines()).strip()
    texte = re.sub(r"^[A-Ha-h][\)\.]\s+", "", texte)
    return " ".join(texte.splitlines()).strip()


def nettoyer_reponse_multi(brut: str) -> str:
    """Nettoie une sortie multi-questions au format 'NUMERO: REPONSE'."""
    texte = re.sub(r"<think>.*?</think>", "", brut, flags=re.DOTALL).strip()
    lignes: list[str] = []
    for ligne in texte.splitlines():
        ligne = ligne.strip().strip("`")
        if not ligne:
            continue
        m = re.match(r"^(?:Question\s+|Q\s*)?(\d+)\s*[:\.\-\)]\s*(.+)$", ligne, flags=re.IGNORECASE)
        if not m:
            continue
        num, rep = m.group(1), m.group(2).strip().strip('"\'`«»')
        m_lettre_courte = re.match(r"^[\(\[]?([A-Ha-h])[\)\.\]]?$", rep)
        m_lettre_pref = re.match(r"^[\(\[]?([A-Ha-h])[\)\.\]]\s+", rep)
        if m_lettre_courte:
            rep_propre = m_lettre_courte.group(1).upper()
        elif m_lettre_pref:
            rep_propre = m_lettre_pref.group(1).upper()
        else:
            rep_propre = nettoyer_reponse(rep, lettre_seule=False)
        lignes.append(f"{num}: {rep_propre}")
    return "\n".join(lignes) if lignes else nettoyer_reponse(texte, lettre_seule=False)


def _trouver_agy() -> str | None:
    """Trouve le binaire agy dans le PATH ou son emplacement Windows standard."""
    trouve = shutil.which("agy")
    if trouve:
        return trouve
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidat = Path(local_app) / "agy" / "bin" / "agy.exe"
        if candidat.is_file():
            return str(candidat)
    candidat_home = Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
    return str(candidat_home) if candidat_home.is_file() else None


def charger_config() -> dict[str, str]:
    """Charge le modèle et l'effort sauvegardés localement."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "model": str(data.get("model") or MODEL),
                "effort": str(data.get("effort") or EFFORT_DEFAUT),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {"model": MODEL, "effort": EFFORT_DEFAUT}


def sauvegarder_config(model: str, effort: str) -> None:
    """Enregistre le choix de modèle et d'effort pour l'utilisateur courant."""
    try:
        CONFIG_PATH.write_text(
            json.dumps({"model": model, "effort": effort}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def parser_sortie_modeles(sortie: str) -> dict[str, dict[str, str]]:
    """Regroupe la sortie de `agy models` par famille et niveau d'effort."""
    catalogue: dict[str, dict[str, str]] = {}
    ordre_map = {nom.lower(): idx for idx, nom in enumerate(EFFORTS_ORDRE)}

    for ligne in sortie.splitlines():
        if "\t" not in ligne:
            continue
        model_id, label = (part.strip() for part in ligne.split("\t", 1))
        if not model_id or not label:
            continue

        m = re.match(r"^(.+?)\s*\((Low|Medium|High|XHigh|Max)\)$", label, flags=re.IGNORECASE)
        if m:
            famille = m.group(1).strip()
            effort = next((e for e in EFFORTS_ORDRE if e.lower() == m.group(2).lower()), m.group(2))
        else:
            famille = label
            effort = "Auto"

        catalogue.setdefault(famille, {})[effort] = model_id

    for famille, efforts in catalogue.items():
        catalogue[famille] = dict(
            sorted(efforts.items(), key=lambda kv: ordre_map.get(kv[0].lower(), 99))
        )
    return catalogue


def lister_modeles_agy() -> dict[str, dict[str, str]]:
    """Récupère les modèles disponibles via `agy models` (avec repli par défaut)."""
    agy_bin = _trouver_agy()
    if not agy_bin:
        return {k: dict(v) for k, v in MODELES_DEFAUT.items()}

    try:
        proc = subprocess.run(
            [agy_bin, "models"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            cwd=tempfile.gettempdir(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            catalogue = parser_sortie_modeles(proc.stdout)
            if catalogue:
                return catalogue
    except (subprocess.TimeoutExpired, OSError):
        pass
    return {k: dict(v) for k, v in MODELES_DEFAUT.items()}


def trouver_famille_et_effort(
    catalogue: dict[str, dict[str, str]],
    model_id: str,
) -> tuple[str, str]:
    """Retrouve (famille, effort) à partir d'un identifiant de modèle `agy`."""
    for famille, efforts in catalogue.items():
        for effort, mid in efforts.items():
            if mid == model_id:
                return famille, effort
    premiere_famille = next(iter(catalogue))
    premier_effort = next(iter(catalogue[premiere_famille]))
    return premiere_famille, premier_effort


def ouvrir_connexion_agy() -> tuple[bool, str]:
    """Ouvre `agy` dans un terminal pour permettre la connexion au compte Google."""
    agy_bin = _trouver_agy()
    if not agy_bin:
        return False, "Installe d'abord Antigravity CLI (agy) puis relance."
    try:
        if os.name == "nt":
            subprocess.Popen(
                ["cmd", "/c", "start", "Connexion Google (agy)", agy_bin],
                cwd=tempfile.gettempdir(),
            )
        else:
            subprocess.Popen([agy_bin], cwd=tempfile.gettempdir())
        return True, "Terminal agy ouvert — connecte-toi avec ton compte Google."
    except OSError as err:
        return False, f"Impossible d'ouvrir agy : {err}"


def _executer_agy(
    prompt: str,
    model: str = MODEL,
    effort: str | None = None,
    timeout: int = TIMEOUT_SECONDS,
    extra_args: list[str] | None = None,
    cwd: str | None = None,
) -> tuple[bool, str]:
    """Exécute `agy -p` et retourne (succès, réponse_brute_ou_erreur)."""
    agy_bin = _trouver_agy()
    if not agy_bin:
        return False, "[erreur] agy introuvable. Installe Antigravity CLI (agy)."

    cmd = [
        agy_bin,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "json",
        "--disable-slash-commands",
    ]
    if effort and effort.lower() != "auto" and not model.lower().endswith(_EFFORT_SUFFIXES):
        cmd.extend(["--effort", effort.lower()])
    if extra_args:
        cmd.extend(extra_args)

    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd or tempfile.gettempdir(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, "[erreur] Délai dépassé."
    except OSError as err:
        return False, f"[erreur] {err}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if any(mot in detail.lower() for mot in ("auth", "login", "credential", "sign in", "token")):
            return False, "[erreur] Non connecté. Clique sur 'Compte' pour te connecter avec Google."
        return False, f"[erreur agy] {detail}"

    try:
        payload = json.loads(proc.stdout)
        return True, payload.get("response", "")
    except json.JSONDecodeError:
        return True, proc.stdout


def interroger_agy(question: str, model: str = MODEL, effort: str | None = None) -> str:
    """Envoie la question à agy en mode non-interactif et retourne uniquement la réponse."""
    numeros = extraire_numeros_questions(question)
    est_multi = len(numeros) >= 2
    avec_lettres = False if est_multi else detecter_lettres(question)
    if est_multi:
        systeme = SYSTEM_MULTI
        timeout = min(120, max(TIMEOUT_SECONDS, 15 + 3 * len(numeros)))
    else:
        systeme = SYSTEM_LETTRE if avec_lettres else SYSTEM_PHRASE
        timeout = TIMEOUT_SECONDS

    ok, brut = _executer_agy(
        f"{systeme}\n\nQuestion :\n{question.strip()}",
        model=model,
        effort=effort,
        timeout=timeout,
    )
    if not ok:
        return brut

    propre = nettoyer_reponse_multi(brut) if est_multi else nettoyer_reponse(brut, lettre_seule=avec_lettres)
    return propre or "[erreur] Réponse vide."


def interroger_agy_image(image_path: str, model: str = MODEL, effort: str | None = None) -> str:
    """Envoie une capture PNG à agy en vision multimodale et supprime le fichier temporaire."""
    chemin = Path(image_path)
    dossier = str(chemin.parent)
    try:
        ok, brut = _executer_agy(
            f"{SYSTEM_IMAGE}\n\nFichier image à analyser : {chemin}",
            model=model,
            effort=effort,
            timeout=max(TIMEOUT_SECONDS, 60),
            extra_args=["--dangerously-skip-permissions", "--add-dir", dossier],
            cwd=dossier,
        )
    finally:
        chemin.unlink(missing_ok=True)

    if not ok:
        return brut

    texte = re.sub(r"<think>.*?</think>", "", brut, flags=re.DOTALL).strip()
    lignes_multi = [
        l for l in texte.splitlines()
        if re.match(r"^(?:Question\s+|Q\s*)?\d+\s*[:\.\-\)]\s*.+$", l.strip(), flags=re.IGNORECASE)
    ]
    if len(lignes_multi) >= 2:
        propre = nettoyer_reponse_multi(texte)
    elif re.match(r"^[\(\[]?[A-Ha-h][\)\.\]]?$", texte.strip()):
        propre = nettoyer_reponse(texte, lettre_seule=True)
    else:
        propre = nettoyer_reponse(texte, lettre_seule=False)
    return propre or "[erreur] Réponse vide."


def traiter_question(question: str, model: str) -> None:
    print("...", end="", flush=True)
    debut = time.monotonic()
    reponse = interroger_agy(question, model=model)
    duree = time.monotonic() - debut
    if "\n" in reponse:
        print(f"\r{reponse}\n({duree:.2f}s)")
    else:
        print(f"\r> {reponse}  ({duree:.2f}s)")


def boucle_auto(
    model: str,
    lire_fn=lire_presse_papiers,
    traiter_fn=traiter_question,
    stop_event: threading.Event | None = None,
) -> None:
    """Surveille le presse-papiers et traite les questions en file FIFO stricte."""
    print(f"qcm.py prêt [{model}] — copie une question (Ctrl+C ici pour quitter)")
    file_attente: queue.Queue[str] = queue.Queue()
    arret = stop_event or threading.Event()
    dernier = (lire_fn() or "").strip()

    def travailleur() -> None:
        while not arret.is_set() or not file_attente.empty():
            try:
                question = file_attente.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                traiter_fn(question, model)
            finally:
                file_attente.task_done()

    thread = threading.Thread(target=travailleur, daemon=True)
    thread.start()

    while not arret.is_set():
        time.sleep(POLL_INTERVAL)
        actuel = lire_fn()
        if actuel is None:
            continue
        actuel_propre = actuel.strip()
        if not actuel_propre or len(actuel_propre) < MIN_TEXT_LENGTH:
            continue
        if actuel_propre == dernier:
            continue

        dernier = actuel_propre
        file_attente.put(actuel_propre)

    file_attente.join()
    thread.join(timeout=1.0)


def boucle_manuelle(model: str) -> None:
    print(f"qcm.py prêt [{model}] — Entrée = analyser presse-papiers | q = quitter")
    while True:
        cmd = input()
        if cmd.strip().lower() == "q":
            break
        texte = (lire_presse_papiers() or "").strip()
        if not texte:
            print("> [presse-papiers vide]")
            continue
        traiter_question(texte, model)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Solveur QCM discret via agy")
    parser.add_argument("question", nargs="*", help="Question directe (optionnel)")
    parser.add_argument("--once", action="store_true", help="Lit le presse-papiers une fois et quitte")
    parser.add_argument("--manual", action="store_true", help="Déclenchement par Entrée au lieu de l'auto")
    parser.add_argument("--model", default=MODEL, help=f"Modèle agy (défaut: {MODEL})")
    args = parser.parse_args()

    try:
        if args.question:
            traiter_question(" ".join(args.question), args.model)
        elif args.once:
            texte = (lire_presse_papiers() or "").strip()
            if not texte:
                print("> [presse-papiers vide]")
                return 1
            traiter_question(texte, args.model)
        elif args.manual:
            boucle_manuelle(args.model)
        else:
            boucle_auto(args.model)
    except KeyboardInterrupt:
        print("\rArrêt.   ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
