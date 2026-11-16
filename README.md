# QCM Assistant (`qcm.exe`)

Mini fenêtre flottante discrète pour Windows qui surveille le presse-papiers et résout des questions/QCM via **Antigravity CLI (`agy`)**, en utilisant le compte Google de chaque utilisateur.

## Fonctionnalités

- **Surveillance automatique du presse-papiers** (`Ctrl+C` texte **et** capture d'écran `Win + Shift + S`, avec file d'attente FIFO stricte et affichage du temps d'exécution).
- **Détection automatique du format** :
  - Choix avec lettres (`A/B/C/D`, `a./b./c./d.`) → renvoie **uniquement la lettre majuscule**.
  - Choix sans lettres → renvoie **uniquement la phrase exacte** de la bonne réponse.
  - Bloc multi-questions (`Question 12 ... Question 13 ...`) → renvoie **`NUMERO: REPONSE`** pour chaque question.
  - Capture d'écran (`Win + Shift + S`) → analyse directement l'image via la vision multimodale d'`agy`.
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
4. Copie (`Ctrl+C`) une question ou fais une capture (`Win + Shift + S`) : la réponse apparaît automatiquement.
   - **`Top`** : garde la fenêtre au-dessus des autres.
   - **`Lire`** (`Entrée` / `F5`) : analyse ce qui est déjà dans le presse-papiers.
   - **`Pause`** / **`Vider`** / **`Compte`** : mettre en pause, effacer l'historique, ou ouvrir la connexion Google `agy`.

## Lancer depuis les sources (Python 3.11+)

```powershell
python qcm_gui.py
```

Mode terminal uniquement :

```powershell
python qcm.py
```

## Compiler `qcm.exe` localement

```powershell
pip install -r requirements.txt
python -m PyInstaller --noconfirm --onefile --windowed --name qcm --distpath . qcm_gui.py
```

## Publier une nouvelle Release sur GitHub

Un workflow GitHub Actions (`.github/workflows/release.yml`) compile automatiquement `qcm.exe` et crée la Release dès qu'un tag `v*` est poussé :

```powershell
git tag v1.0.0
git push origin v1.0.0
```
