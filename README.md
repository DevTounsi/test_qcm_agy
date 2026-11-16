# QCM Assistant (`qcm.exe`)

Mini fenêtre flottante au design **macOS / Apple** (Tauri v2 + WebView2) pour Windows : surveille le presse-papiers et résout des questions/QCM via **Antigravity CLI (`agy`)**, avec le compte Google de chaque utilisateur.

## Fonctionnalités

- **Interface style Apple / macOS** : fenêtre flottante sombre sans bordures, coins arrondis, effet verre dépoli, feux tricolores (`Fermer` / `Réduire`) et contrôles compacts.
- **Surveillance automatique du presse-papiers** (`Ctrl+C` texte **et** capture d'écran `Win + Shift + S`, avec vidage automatique de l'image après lecture, file FIFO stricte et temps d'exécution).
- **Capture plein écran (`Écran` / `F9`)** : masque temporairement la fenêtre hors du premier plan, capture tout l'écran, puis réaffiche la fenêtre.
- **Détection automatique du format** :
  - Choix avec lettres (`A/B/C/D`, `a./b./c./d.`) → renvoie **uniquement la lettre majuscule**.
  - Choix sans lettres → renvoie **uniquement la phrase exacte** de la bonne réponse.
  - Bloc multi-questions (`Question 12 ... Question 13 ...`) → renvoie **`NUMERO: REPONSE`** pour chaque question.
- **Sélecteur de modèle et d'effort** directement dans l'interface (chargés depuis `agy models` et mémorisés dans `~/.qcm_agy_config.json`).
- **Connexion Google par utilisateur** via le bouton **`Compte`** qui ouvre `agy`.

## Prérequis (obligatoire pour chaque utilisateur)

1. Avoir **Antigravity CLI (`agy`)** installé sur Windows.
2. Être connecté à son **compte Google** dans `agy` :
   - Soit en lançant `agy` une première fois dans un terminal.
   - Soit directement en cliquant sur le bouton **`Compte`** dans `qcm.exe`.

## Utilisation (Windows)

1. Télécharge **`qcm.exe`** depuis la page **Releases** du dépôt GitHub.
2. Double-clique sur **`qcm.exe`**.
3. Choisis ton **Modèle** et ton **Effort** (ex. `Gemini 4 Hydrogen` + `Medium`, ou `Gemini 3.8 Flash` + `Low`).
4. Copie (`Ctrl+C`) une question, fais une capture (`Win + Shift + S`), ou appuie sur **`Écran`** (`F9`) : la réponse apparaît automatiquement.
   - **`Top`** : garde la fenêtre au-dessus des autres.
   - **`Écran`** (`F9`) : masque la fenêtre, capture tout l'écran et réaffiche la fenêtre.
   - **`Lire`** (`Entrée` / `F5`) : analyse ce qui est déjà dans le presse-papiers.
   - **`Pause`** / **`Vider`** / **`Compte`** : mettre en pause, effacer l'historique, ou ouvrir la connexion Google `agy`.

## Compiler la version Tauri (`qcm.exe`)

Prérequis de compilation : Rust (`cargo`) + MSVC Build Tools + WebView2 (préinstallés sur GitHub Actions `windows-latest`).

```powershell
cargo build --release --manifest-path src-tauri/Cargo.toml
```

Le binaire autonome est généré dans `src-tauri/target/release/qcm.exe`.

## Version Python (CLI / Tkinter de secours)

```powershell
python qcm.py       # mode terminal
python qcm_gui.py   # interface Tkinter
```
