use image::RgbaImage;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub const DEFAULT_MODEL: &str = "gemini-4-hydrogen-medium";
pub const DEFAULT_EFFORT: &str = "Medium";
pub const MIN_TEXT_LENGTH: usize = 6;
const DEFAULT_TIMEOUT_SECS: u64 = 45;
const EFFORT_SUFFIXES: [&str; 5] = ["-low", "-medium", "-high", "-xhigh", "-max"];

const SYSTEM_PHRASE: &str = "Tu es un moteur QCM silencieux. Ne fais aucun appel d'outil et n'explique rien. \
Réponds UNIQUEMENT par la phrase exacte de la bonne réponse, \
sans lettre d'option (pas de A), B), C., etc.), sans préfixe, sans guillemets ni Markdown.";

const SYSTEM_LETTRE: &str = "Tu es un moteur QCM silencieux. Ne fais aucun appel d'outil et n'explique rien. \
Les choix sont identifiés par des lettres. \
Réponds UNIQUEMENT par la lettre majuscule de la bonne réponse (ex: A, B, C ou D), \
sans parenthèse, sans point, sans texte, sans guillemets ni Markdown.";

const SYSTEM_MULTI: &str = "Tu es un moteur QCM silencieux. Ne fais aucun appel d'outil et n'explique rien. \
Le texte contient plusieurs questions numérotées. \
Pour CHAQUE question, réponds sur une ligne au format exact `NUMERO: REPONSE` \
(où REPONSE est uniquement la lettre majuscule si des options a/b/c/d sont présentes, \
sinon la phrase exacte de la bonne réponse). Aucune autre ligne, aucun préfixe, aucun Markdown.";

const SYSTEM_IMAGE: &str = "Tu es un moteur QCM silencieux. Utilise uniquement ton outil de lecture pour lire le fichier image indiqué, \
repère la ou les questions visibles (ignore les menus/barres autour), puis n'explique rien.\n\
- S'il y a UNE seule question : si les choix ont des lettres (A/B/C/D, a/b/c/d), \
réponds UNIQUEMENT par la lettre majuscule ; sinon, réponds UNIQUEMENT par la phrase exacte de la bonne réponse.\n\
- S'il y a PLUSIEURS questions numérotées, réponds sur des lignes au format exact `NUMERO: REPONSE`.\n\
Aucun préfixe, aucune explication, aucun Markdown.";

pub type Catalogue = BTreeMap<String, BTreeMap<String, String>>;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AppConfig {
    pub model: String,
    pub effort: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            model: DEFAULT_MODEL.to_string(),
            effort: DEFAULT_EFFORT.to_string(),
        }
    }
}

pub enum ClipboardContent {
    Locked,
    Empty,
    Text(String),
    ImagePath(PathBuf),
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE")
        .or_else(|| std::env::var_os("HOME"))
        .map(PathBuf::from)
}

fn config_path() -> Option<PathBuf> {
    home_dir().map(|h| h.join(".qcm_agy_config.json"))
}

pub fn load_config() -> AppConfig {
    if let Some(path) = config_path() {
        if let Ok(raw) = fs::read_to_string(path) {
            if let Ok(cfg) = serde_json::from_str::<AppConfig>(&raw) {
                if !cfg.model.is_empty() {
                    return cfg;
                }
            }
        }
    }
    AppConfig::default()
}

pub fn save_config(model: &str, effort: &str) {
    if let Some(path) = config_path() {
        let cfg = AppConfig {
            model: model.to_string(),
            effort: effort.to_string(),
        };
        if let Ok(json) = serde_json::to_string_pretty(&cfg) {
            let _ = fs::write(path, json);
        }
    }
}

pub fn default_catalogue() -> Catalogue {
    let entries: &[(&str, &[(&str, &str)])] = &[
        (
            "Gemini 4 Hydrogen",
            &[
                ("Low", "gemini-4-hydrogen-low"),
                ("Medium", "gemini-4-hydrogen-medium"),
                ("High", "gemini-4-hydrogen-high"),
                ("XHigh", "gemini-4-hydrogen-xhigh"),
            ],
        ),
        (
            "Gemini 4 Argon",
            &[
                ("Low", "gemini-4-argon-low"),
                ("Medium", "gemini-4-argon-medium"),
                ("High", "gemini-4-argon-high"),
            ],
        ),
        (
            "Gemini 3.8 Flash",
            &[
                ("Low", "gemini-3.8-flash-low"),
                ("Medium", "gemini-3.8-flash-medium"),
                ("High", "gemini-3.8-flash-high"),
            ],
        ),
        (
            "Claude Sonnet 5.5",
            &[
                ("Low", "claude-sonnet-5-5-low"),
                ("Medium", "claude-sonnet-5-5-medium"),
                ("High", "claude-sonnet-5-5-high"),
            ],
        ),
        (
            "Claude Opus 5.5",
            &[
                ("Low", "claude-opus-5-5-low"),
                ("Medium", "claude-opus-5-5-medium"),
                ("High", "claude-opus-5-5-high"),
            ],
        ),
        ("GPT-OSS 120B", &[("Medium", "gpt-oss-120b-medium")]),
    ];

    let mut cat = BTreeMap::new();
    for (fam, items) in entries {
        let mut map = BTreeMap::new();
        for (eff, id) in *items {
            map.insert((*eff).to_string(), (*id).to_string());
        }
        cat.insert((*fam).to_string(), map);
    }
    cat
}

pub fn find_agy() -> Option<PathBuf> {
    if let Some(paths) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&paths) {
            for name in ["agy.exe", "agy.cmd", "agy"] {
                let candidate = dir.join(name);
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
        }
    }
    if let Some(local) = std::env::var_os("LOCALAPPDATA") {
        let candidate = PathBuf::from(local).join("agy").join("bin").join("agy.exe");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    if let Some(home) = home_dir() {
        let candidate = home.join("AppData").join("Local").join("agy").join("bin").join("agy.exe");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

pub fn parse_models_output(output: &str) -> Catalogue {
    static RE_EFFORT: OnceLock<Regex> = OnceLock::new();
    let re = RE_EFFORT
        .get_or_init(|| Regex::new(r"(?i)^(.+?)\s*\((Low|Medium|High|XHigh|Max)\)$").unwrap());

    let mut cat = Catalogue::new();
    for line in output.lines() {
        let Some((id_raw, label_raw)) = line.split_once('\t') else {
            continue;
        };
        let model_id = id_raw.trim();
        let label = label_raw.trim();
        if model_id.is_empty() || label.is_empty() {
            continue;
        }

        let (family, effort) = if let Some(caps) = re.captures(label) {
            let fam = caps.get(1).map(|m| m.as_str().trim()).unwrap_or(label);
            let eff_raw = caps.get(2).map(|m| m.as_str()).unwrap_or("Auto");
            let eff = match eff_raw.to_lowercase().as_str() {
                "low" => "Low",
                "medium" => "Medium",
                "high" => "High",
                "xhigh" => "XHigh",
                "max" => "Max",
                _ => eff_raw,
            };
            (fam.to_string(), eff.to_string())
        } else {
            (label.to_string(), "Auto".to_string())
        };

        cat.entry(family)
            .or_default()
            .insert(effort, model_id.to_string());
    }
    cat
}

pub fn list_agy_models() -> Catalogue {
    let Some(agy_bin) = find_agy() else {
        return default_catalogue();
    };
    let mut cmd = Command::new(agy_bin);
    cmd.arg("models")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .current_dir(std::env::temp_dir());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    if let Ok((code, stdout, _)) = run_with_timeout(cmd, Duration::from_secs(10)) {
        if code == 0 {
            let parsed = parse_models_output(&stdout);
            if !parsed.is_empty() {
                return parsed;
            }
        }
    }
    default_catalogue()
}

pub fn find_family_and_effort(catalogue: &Catalogue, model_id: &str) -> (String, String) {
    for (fam, efforts) in catalogue {
        for (eff, id) in efforts {
            if id == model_id {
                return (fam.clone(), eff.clone());
            }
        }
    }
    if let Some((fam, efforts)) = catalogue.iter().next() {
        let eff = efforts
            .keys()
            .find(|k| k.as_str() == "Medium")
            .or_else(|| efforts.keys().next())
            .cloned()
            .unwrap_or_else(|| "Medium".to_string());
        return (fam.clone(), eff);
    }
    ("Gemini 4 Hydrogen".to_string(), "Medium".to_string())
}

pub fn open_agy_login() -> (bool, String) {
    let Some(agy_bin) = find_agy() else {
        return (
            false,
            "Installe d'abord Antigravity CLI (agy) puis relance.".to_string(),
        );
    };
    #[cfg(windows)]
    let res = Command::new("cmd")
        .args(["/c", "start", "Connexion Google (agy)"])
        .arg(&agy_bin)
        .current_dir(std::env::temp_dir())
        .spawn();
    #[cfg(not(windows))]
    let res = Command::new(&agy_bin)
        .current_dir(std::env::temp_dir())
        .spawn();

    match res {
        Ok(_) => (
            true,
            "Terminal agy ouvert — connecte-toi avec ton compte Google.".to_string(),
        ),
        Err(err) => (false, format!("Impossible d'ouvrir agy : {err}")),
    }
}

pub fn detect_letters(question: &str) -> bool {
    static RE1: OnceLock<Regex> = OnceLock::new();
    static RE2: OnceLock<Regex> = OnceLock::new();
    static RE3: OnceLock<Regex> = OnceLock::new();

    let re1 = RE1.get_or_init(|| Regex::new(r"(?:^|\s)[\(\[]?([A-Ha-h])[\)\.\]]\s+\S").unwrap());
    let re2 = RE2.get_or_init(|| Regex::new(r"(?m)^\s*([A-Ha-h])\s*[-:]\s+\S").unwrap());
    let re3 = RE3.get_or_init(|| Regex::new(r"(?m)^\s*([A-Ha-h])[\)\.]?\s*$").unwrap());

    let mut letters = HashSet::new();
    for re in [re1, re2, re3] {
        for caps in re.captures_iter(question) {
            if let Some(m) = caps.get(1) {
                letters.insert(m.as_str().to_ascii_uppercase());
            }
        }
    }
    letters.len() >= 2 && letters.contains("A") && letters.contains("B")
}

pub fn extract_question_numbers(text: &str) -> Vec<String> {
    static RE_Q: OnceLock<Regex> = OnceLock::new();
    let re = RE_Q.get_or_init(|| {
        Regex::new(r"(?im)^[ \t]*(?:Question\s+|Q\s*)(\d+)\b([^\r\n]*)").unwrap()
    });

    let mut seen = HashSet::new();
    let mut nums = Vec::new();
    for caps in re.captures_iter(text) {
        let rest = caps
            .get(2)
            .map(|m| m.as_str().trim_start().to_lowercase())
            .unwrap_or_default();
        if rest.starts_with("réponse") || rest.starts_with("reponse") {
            continue;
        }
        if let Some(m) = caps.get(1) {
            let n = m.as_str().to_string();
            if seen.insert(n.clone()) {
                nums.push(n);
            }
        }
    }
    if nums.len() >= 2 {
        nums
    } else {
        Vec::new()
    }
}

fn strip_think_tags(text: &str) -> String {
    static RE_THINK: OnceLock<Regex> = OnceLock::new();
    let re = RE_THINK.get_or_init(|| Regex::new(r"(?s)<think>.*?</think>").unwrap());
    re.replace_all(text, "").to_string()
}

pub fn clean_answer(raw: &str, single_letter: bool) -> String {
    static RE_BOLD: OnceLock<Regex> = OnceLock::new();
    static RE_PREFIX: OnceLock<Regex> = OnceLock::new();
    static RE_LETTER: OnceLock<Regex> = OnceLock::new();
    static RE_OPT_PREFIX: OnceLock<Regex> = OnceLock::new();

    let mut text = strip_think_tags(raw);
    text = text
        .trim()
        .trim_matches(|c| matches!(c, '"' | '\'' | '`' | '«' | '»'))
        .to_string();
    let re_bold = RE_BOLD.get_or_init(|| Regex::new(r"(?s)^\*\*(.*)\*\*$").unwrap());
    text = re_bold.replace(&text, "$1").trim().to_string();

    let re_prefix = RE_PREFIX
        .get_or_init(|| Regex::new(r"(?i)^(?:Réponse|Reponse|Answer)\s*:\s*").unwrap());
    text = re_prefix.replace(&text, "").to_string();

    if single_letter {
        let re_letter =
            RE_LETTER.get_or_init(|| Regex::new(r"^[\(\[]?([A-Ha-h])[\)\]]?(?:[\.\:\-\s]|$)").unwrap());
        if let Some(caps) = re_letter.captures(&text) {
            if let Some(m) = caps.get(1) {
                return m.as_str().to_ascii_uppercase();
            }
        }
    } else {
        let re_opt = RE_OPT_PREFIX.get_or_init(|| Regex::new(r"^[A-Ha-h][\)\.]\s+").unwrap());
        text = re_opt.replace(&text, "").to_string();
    }

    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn clean_multi_answer(raw: &str) -> String {
    static RE_LINE: OnceLock<Regex> = OnceLock::new();
    static RE_SHORT_LETTER: OnceLock<Regex> = OnceLock::new();
    static RE_PREF_LETTER: OnceLock<Regex> = OnceLock::new();

    let re_line = RE_LINE
        .get_or_init(|| Regex::new(r"(?i)^(?:Question\s+|Q\s*)?(\d+)\s*[:\.\-\)]\s*(.+)$").unwrap());
    let re_short =
        RE_SHORT_LETTER.get_or_init(|| Regex::new(r"^[\(\[]?([A-Ha-h])[\)\.\]]?$").unwrap());
    let re_pref =
        RE_PREF_LETTER.get_or_init(|| Regex::new(r"^[\(\[]?([A-Ha-h])[\)\.\]]\s+").unwrap());

    let text = strip_think_tags(raw);
    let mut lines = Vec::new();

    for raw_line in text.lines() {
        let line = raw_line.trim().trim_matches('`');
        if line.is_empty() {
            continue;
        }
        let Some(caps) = re_line.captures(line) else {
            continue;
        };
        let num = caps.get(1).map(|m| m.as_str()).unwrap_or("");
        let rep = caps
            .get(2)
            .map(|m| {
                m.as_str()
                    .trim()
                    .trim_matches(|c| matches!(c, '"' | '\'' | '`' | '«' | '»'))
            })
            .unwrap_or("");

        let clean_rep = if let Some(c) = re_short.captures(rep) {
            c[1].to_ascii_uppercase()
        } else if let Some(c) = re_pref.captures(rep) {
            c[1].to_ascii_uppercase()
        } else {
            clean_answer(rep, false)
        };
        lines.push(format!("{num}: {clean_rep}"));
    }

    if lines.is_empty() {
        clean_answer(&text, false)
    } else {
        lines.join("\n")
    }
}

#[cfg(windows)]
pub fn clipboard_sequence_number() -> u32 {
    unsafe { windows_sys::Win32::System::DataExchange::GetClipboardSequenceNumber() }
}

#[cfg(not(windows))]
pub fn clipboard_sequence_number() -> u32 {
    0
}

fn temp_image_path(prefix: &str) -> Result<PathBuf, String> {
    let dir = std::env::temp_dir().join("qcm_clip_imgs");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    Ok(dir.join(format!("{prefix}_{nanos}.png")))
}

pub fn read_clipboard_content() -> ClipboardContent {
    let mut cb = match arboard::Clipboard::new() {
        Ok(cb) => cb,
        Err(arboard::Error::ClipboardOccupied) => return ClipboardContent::Locked,
        Err(_) => return ClipboardContent::Empty,
    };

    match cb.get_text() {
        Ok(text) => {
            let trimmed = text.trim();
            if trimmed.chars().count() >= MIN_TEXT_LENGTH {
                return ClipboardContent::Text(trimmed.to_string());
            }
        }
        Err(arboard::Error::ClipboardOccupied) => return ClipboardContent::Locked,
        Err(_) => {}
    }

    match cb.get_image() {
        Ok(img) => {
            if let Some(rgba) =
                RgbaImage::from_raw(img.width as u32, img.height as u32, img.bytes.into_owned())
            {
                if let Ok(path) = temp_image_path("clip") {
                    if rgba.save(&path).is_ok() {
                        let _ = cb.clear();
                        return ClipboardContent::ImagePath(path);
                    }
                }
            }
            ClipboardContent::Empty
        }
        Err(arboard::Error::ClipboardOccupied) => ClipboardContent::Locked,
        Err(_) => ClipboardContent::Empty,
    }
}

#[cfg(windows)]
pub fn capture_fullscreen() -> Option<PathBuf> {
    use std::ptr::null_mut;
    use windows_sys::Win32::Graphics::Gdi::{
        BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DeleteDC, DeleteObject, GetDC,
        GetDIBits, ReleaseDC, SelectObject, BITMAPINFO, BITMAPINFOHEADER, BI_RGB, DIB_RGB_COLORS,
        SRCCOPY,
    };
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
        SM_YVIRTUALSCREEN,
    };

    unsafe {
        let x = GetSystemMetrics(SM_XVIRTUALSCREEN);
        let y = GetSystemMetrics(SM_YVIRTUALSCREEN);
        let w = GetSystemMetrics(SM_CXVIRTUALSCREEN);
        let h = GetSystemMetrics(SM_CYVIRTUALSCREEN);
        if w <= 0 || h <= 0 {
            return None;
        }

        let hdc_screen = GetDC(null_mut());
        if hdc_screen.is_null() {
            return None;
        }
        let hdc_mem = CreateCompatibleDC(hdc_screen);
        if hdc_mem.is_null() {
            ReleaseDC(null_mut(), hdc_screen);
            return None;
        }
        let hbmp = CreateCompatibleBitmap(hdc_screen, w, h);
        if hbmp.is_null() {
            DeleteDC(hdc_mem);
            ReleaseDC(null_mut(), hdc_screen);
            return None;
        }

        let old = SelectObject(hdc_mem, hbmp);
        let ok = BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, SRCCOPY) != 0;

        let mut pixels = vec![0u8; (w as usize) * (h as usize) * 4];
        let mut bmi: BITMAPINFO = std::mem::zeroed();
        bmi.bmiHeader.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        bmi.bmiHeader.biWidth = w;
        bmi.bmiHeader.biHeight = -h;
        bmi.bmiHeader.biPlanes = 1;
        bmi.bmiHeader.biBitCount = 32;
        bmi.bmiHeader.biCompression = BI_RGB;

        let rows = if ok {
            GetDIBits(
                hdc_mem,
                hbmp,
                0,
                h as u32,
                pixels.as_mut_ptr() as *mut _,
                &mut bmi,
                DIB_RGB_COLORS,
            )
        } else {
            0
        };

        SelectObject(hdc_mem, old);
        DeleteObject(hbmp);
        DeleteDC(hdc_mem);
        ReleaseDC(null_mut(), hdc_screen);

        if rows == 0 {
            return None;
        }

        // BGRA -> RGBA (opaque)
        for chunk in pixels.chunks_exact_mut(4) {
            chunk.swap(0, 2);
            chunk[3] = 255;
        }

        let rgba = RgbaImage::from_raw(w as u32, h as u32, pixels)?;
        let path = temp_image_path("screen").ok()?;
        rgba.save(&path).ok()?;
        Some(path)
    }
}

#[cfg(not(windows))]
pub fn capture_fullscreen() -> Option<PathBuf> {
    None
}

fn run_with_timeout(mut cmd: Command, timeout: Duration) -> Result<(i32, String, String), String> {
    let mut child = cmd.spawn().map_err(|e| format!("[erreur] {e}"))?;

    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();

    let out_handle = thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(ref mut p) = stdout_pipe {
            let _ = p.read_to_end(&mut buf);
        }
        String::from_utf8_lossy(&buf).into_owned()
    });

    let err_handle = thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(ref mut p) = stderr_pipe {
            let _ = p.read_to_end(&mut buf);
        }
        String::from_utf8_lossy(&buf).into_owned()
    });

    let start = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break st,
            Ok(None) => {
                if start.elapsed() >= timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    let _ = out_handle.join();
                    let _ = err_handle.join();
                    return Err("[erreur] Délai dépassé.".to_string());
                }
                thread::sleep(Duration::from_millis(80));
            }
            Err(err) => {
                let _ = child.kill();
                return Err(format!("[erreur] {err}"));
            }
        }
    };

    let stdout = out_handle.join().unwrap_or_default();
    let stderr = err_handle.join().unwrap_or_default();
    Ok((status.code().unwrap_or(-1), stdout, stderr))
}

#[derive(Deserialize)]
struct AgyJsonResponse {
    response: Option<String>,
}

fn execute_agy(
    prompt: &str,
    model: &str,
    effort: Option<&str>,
    timeout_secs: u64,
    extra_args: &[&str],
    cwd: &Path,
) -> Result<String, String> {
    let Some(agy_bin) = find_agy() else {
        return Err("[erreur] agy introuvable. Installe Antigravity CLI (agy).".to_string());
    };

    let mut cmd = Command::new(agy_bin);
    cmd.arg("-p")
        .arg(prompt)
        .arg("--model")
        .arg(model)
        .arg("--output-format")
        .arg("json")
        .arg("--disable-slash-commands");

    if let Some(eff) = effort {
        let eff_low = eff.to_lowercase();
        let mod_low = model.to_lowercase();
        if eff_low != "auto" && !EFFORT_SUFFIXES.iter().any(|s| mod_low.ends_with(s)) {
            cmd.arg("--effort").arg(eff_low);
        }
    }

    for arg in extra_args {
        cmd.arg(arg);
    }

    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .current_dir(cwd);
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);

    let (code, stdout, stderr) = run_with_timeout(cmd, Duration::from_secs(timeout_secs))?;

    if code != 0 {
        let detail = if stderr.trim().is_empty() {
            stdout.trim()
        } else {
            stderr.trim()
        };
        let lower = detail.to_lowercase();
        if ["auth", "login", "credential", "sign in", "token"]
            .iter()
            .any(|k| lower.contains(k))
        {
            return Err(
                "[erreur] Non connecté. Clique sur 'Compte' pour te connecter avec Google."
                    .to_string(),
            );
        }
        return Err(format!("[erreur agy] {detail}"));
    }

    if let Ok(parsed) = serde_json::from_str::<AgyJsonResponse>(&stdout) {
        if let Some(resp) = parsed.response {
            return Ok(resp);
        }
    }
    Ok(stdout)
}

pub fn ask_agy_text(question: &str, model: &str, effort: Option<&str>) -> (bool, String) {
    let nums = extract_question_numbers(question);
    let is_multi = nums.len() >= 2;
    let has_letters = !is_multi && detect_letters(question);

    let (system, timeout) = if is_multi {
        (
            SYSTEM_MULTI,
            (15 + 3 * nums.len() as u64).clamp(DEFAULT_TIMEOUT_SECS, 120),
        )
    } else if has_letters {
        (SYSTEM_LETTRE, DEFAULT_TIMEOUT_SECS)
    } else {
        (SYSTEM_PHRASE, DEFAULT_TIMEOUT_SECS)
    };

    let prompt = format!("{system}\n\nQuestion :\n{}", question.trim());
    match execute_agy(&prompt, model, effort, timeout, &[], &std::env::temp_dir()) {
        Ok(raw) => {
            let clean = if is_multi {
                clean_multi_answer(&raw)
            } else {
                clean_answer(&raw, has_letters)
            };
            if clean.is_empty() {
                (false, "[erreur] Réponse vide.".to_string())
            } else {
                (true, clean)
            }
        }
        Err(err) => (false, err),
    }
}

pub fn ask_agy_image(image_path: &Path, model: &str, effort: Option<&str>) -> (bool, String) {
    static RE_MULTI_LINE: OnceLock<Regex> = OnceLock::new();
    static RE_SINGLE_LET: OnceLock<Regex> = OnceLock::new();

    let dir = image_path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(std::env::temp_dir);
    let dir_str = dir.to_string_lossy().into_owned();
    let prompt = format!("{SYSTEM_IMAGE}\n\nFichier image à analyser : {}", image_path.display());

    let result = execute_agy(
        &prompt,
        model,
        effort,
        60,
        &["--dangerously-skip-permissions", "--add-dir", &dir_str],
        &dir,
    );
    let _ = fs::remove_file(image_path);

    match result {
        Ok(raw) => {
            let text = strip_think_tags(&raw).trim().to_string();
            let re_multi = RE_MULTI_LINE.get_or_init(|| {
                Regex::new(r"(?i)^(?:Question\s+|Q\s*)?\d+\s*[:\.\-\)]\s*.+$").unwrap()
            });
            let multi_count = text
                .lines()
                .filter(|l| re_multi.is_match(l.trim()))
                .count();
            let re_let = RE_SINGLE_LET
                .get_or_init(|| Regex::new(r"^[\(\[]?[A-Ha-h][\)\.\]]?$").unwrap());

            let clean = if multi_count >= 2 {
                clean_multi_answer(&text)
            } else if re_let.is_match(text.trim()) {
                clean_answer(&text, true)
            } else {
                clean_answer(&text, false)
            };
            if clean.is_empty() {
                (false, "[erreur] Réponse vide.".to_string())
            } else {
                (true, clean)
            }
        }
        Err(err) => (false, err),
    }
}
