mod qcm_core;

use qcm_core::{
    ask_agy_image, ask_agy_text, capture_fullscreen, clipboard_sequence_number, default_catalogue,
    find_agy, find_family_and_effort, list_agy_models, load_config, open_agy_login,
    read_clipboard_content, save_config, Catalogue, ClipboardContent,
};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter, Manager, State};

enum TaskPayload {
    Text(String),
    Image(PathBuf),
}

struct TaskItem {
    idx: usize,
    payload: TaskPayload,
}

#[derive(Clone, Serialize)]
struct StatusEvent {
    busy: bool,
    queued: usize,
    paused: bool,
    last_duration: Option<f64>,
}

#[derive(Clone, Serialize)]
struct ResultEvent {
    idx: usize,
    answer: String,
    duration: f64,
    is_error: bool,
}

#[derive(Clone, Serialize)]
struct CatalogueEvent {
    catalogue: Catalogue,
    family: String,
    effort: String,
}

#[derive(Clone, Serialize)]
struct NoticeEvent {
    ok: bool,
    message: String,
}

#[derive(Serialize)]
struct InitialState {
    catalogue: Catalogue,
    family: String,
    effort: String,
    topmost: bool,
    paused: bool,
    agy_installed: bool,
}

#[derive(Serialize)]
struct AccountResponse {
    ok: bool,
    message: String,
}

struct AppState {
    catalogue: Mutex<Catalogue>,
    model: Mutex<String>,
    effort: Mutex<String>,
    topmost: AtomicBool,
    paused: AtomicBool,
    busy: AtomicBool,
    queued: AtomicUsize,
    counter: AtomicUsize,
    last_seq: AtomicU32,
    last_duration: Mutex<Option<f64>>,
    capturing: AtomicBool,
    tx: mpsc::Sender<TaskItem>,
}

impl AppState {
    fn emit_status(&self, app: &AppHandle) {
        let payload = StatusEvent {
            busy: self.busy.load(Ordering::Relaxed),
            queued: self.queued.load(Ordering::Relaxed),
            paused: self.paused.load(Ordering::Relaxed),
            last_duration: *self.last_duration.lock().unwrap(),
        };
        let _ = app.emit("qcm:status", payload);
    }

    fn enqueue(&self, app: &AppHandle, payload: TaskPayload) {
        let idx = self.counter.fetch_add(1, Ordering::Relaxed) + 1;
        self.queued.fetch_add(1, Ordering::Relaxed);
        self.emit_status(app);
        let _ = self.tx.send(TaskItem { idx, payload });
    }

    fn process_current_clipboard(&self, app: &AppHandle) -> bool {
        match read_clipboard_content() {
            ClipboardContent::Locked => false,
            ClipboardContent::Empty => true,
            ClipboardContent::Text(txt) => {
                self.enqueue(app, TaskPayload::Text(txt));
                true
            }
            ClipboardContent::ImagePath(path) => {
                self.enqueue(app, TaskPayload::Image(path));
                true
            }
        }
    }
}

#[tauri::command]
fn get_initial_state(state: State<'_, Arc<AppState>>) -> InitialState {
    let cat = state.catalogue.lock().unwrap().clone();
    let model_id = state.model.lock().unwrap().clone();
    let (family, effort) = find_family_and_effort(&cat, &model_id);
    InitialState {
        catalogue: cat,
        family,
        effort,
        topmost: state.topmost.load(Ordering::Relaxed),
        paused: state.paused.load(Ordering::Relaxed),
        agy_installed: find_agy().is_some(),
    }
}

#[tauri::command]
fn close_app(app: AppHandle) {
    app.exit(0);
}

#[tauri::command]
fn minimize_app(app: AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.minimize();
    }
}

#[tauri::command]
fn set_topmost(app: AppHandle, state: State<'_, Arc<AppState>>, topmost: bool) {
    state.topmost.store(topmost, Ordering::Relaxed);
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.set_always_on_top(topmost);
    }
}

#[tauri::command]
fn set_paused(app: AppHandle, state: State<'_, Arc<AppState>>, paused: bool) {
    state.paused.store(paused, Ordering::Relaxed);
    state.emit_status(&app);
}

#[tauri::command]
fn reset_counter(state: State<'_, Arc<AppState>>) {
    state.counter.store(0, Ordering::Relaxed);
    *state.last_duration.lock().unwrap() = None;
}

#[tauri::command]
fn set_model_effort(
    state: State<'_, Arc<AppState>>,
    family: String,
    effort: String,
) -> Option<(String, String)> {
    let cat = state.catalogue.lock().unwrap();
    let efforts = cat.get(&family)?;
    let chosen_effort = if efforts.contains_key(&effort) {
        effort
    } else if efforts.contains_key("Medium") {
        "Medium".to_string()
    } else {
        efforts.keys().next()?.clone()
    };
    let model_id = efforts.get(&chosen_effort)?.clone();
    drop(cat);

    *state.model.lock().unwrap() = model_id.clone();
    *state.effort.lock().unwrap() = chosen_effort.clone();
    save_config(&model_id, &chosen_effort);
    Some((model_id, chosen_effort))
}

#[tauri::command]
fn open_account() -> AccountResponse {
    let (ok, message) = open_agy_login();
    AccountResponse { ok, message }
}

#[tauri::command]
fn read_clipboard_now(app: AppHandle, state: State<'_, Arc<AppState>>) {
    let _ = state.process_current_clipboard(&app);
    state
        .last_seq
        .store(clipboard_sequence_number(), Ordering::Relaxed);
}

#[tauri::command]
fn capture_screen(app: AppHandle, state: State<'_, Arc<AppState>>) {
    if state.capturing.swap(true, Ordering::SeqCst) {
        return;
    }
    let state_arc = Arc::clone(&state);
    thread::spawn(move || {
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.set_always_on_top(false);
            let _ = win.hide();
        }
        thread::sleep(Duration::from_millis(220));

        let captured = capture_fullscreen();

        if let Some(win) = app.get_webview_window("main") {
            let _ = win.show();
            let _ = win.set_always_on_top(state_arc.topmost.load(Ordering::Relaxed));
        }
        state_arc.capturing.store(false, Ordering::SeqCst);

        match captured {
            Some(path) => state_arc.enqueue(&app, TaskPayload::Image(path)),
            None => {
                let _ = app.emit(
                    "qcm:notice",
                    NoticeEvent {
                        ok: false,
                        message: "[erreur] Capture d'écran impossible.".to_string(),
                    },
                );
            }
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let cfg = load_config();
    let cat = default_catalogue();
    let (fam, eff) = find_family_and_effort(&cat, &cfg.model);
    let initial_model = cat
        .get(&fam)
        .and_then(|m| m.get(&eff))
        .cloned()
        .unwrap_or(cfg.model);

    let (tx, rx) = mpsc::channel::<TaskItem>();
    let state = Arc::new(AppState {
        catalogue: Mutex::new(cat),
        model: Mutex::new(initial_model),
        effort: Mutex::new(eff),
        topmost: AtomicBool::new(true),
        paused: AtomicBool::new(false),
        busy: AtomicBool::new(false),
        queued: AtomicUsize::new(0),
        counter: AtomicUsize::new(0),
        last_seq: AtomicU32::new(clipboard_sequence_number()),
        last_duration: Mutex::new(None),
        capturing: AtomicBool::new(false),
        tx,
    });

    let state_setup = Arc::clone(&state);

    tauri::Builder::default()
        .manage(state)
        .invoke_handler(tauri::generate_handler![
            get_initial_state,
            close_app,
            minimize_app,
            set_topmost,
            set_paused,
            reset_counter,
            set_model_effort,
            open_account,
            read_clipboard_now,
            capture_screen
        ])
        .setup(move |app| {
            let app_worker = app.handle().clone();
            let state_worker = Arc::clone(&state_setup);
            thread::spawn(move || {
                while let Ok(item) = rx.recv() {
                    state_worker.queued.fetch_sub(1, Ordering::Relaxed);
                    state_worker.busy.store(true, Ordering::Relaxed);
                    state_worker.emit_status(&app_worker);

                    let model = state_worker.model.lock().unwrap().clone();
                    let effort = state_worker.effort.lock().unwrap().clone();
                    let start = Instant::now();

                    let (ok, answer) = match item.payload {
                        TaskPayload::Text(q) => ask_agy_text(&q, &model, Some(&effort)),
                        TaskPayload::Image(path) => ask_agy_image(&path, &model, Some(&effort)),
                    };

                    let duration = start.elapsed().as_secs_f64();
                    *state_worker.last_duration.lock().unwrap() = Some(duration);
                    state_worker.busy.store(false, Ordering::Relaxed);

                    let _ = app_worker.emit(
                        "qcm:result",
                        ResultEvent {
                            idx: item.idx,
                            answer,
                            duration,
                            is_error: !ok,
                        },
                    );
                    state_worker.emit_status(&app_worker);
                }
            });

            let app_clip = app.handle().clone();
            let state_clip = Arc::clone(&state_setup);
            thread::spawn(move || loop {
                thread::sleep(Duration::from_millis(150));
                if state_clip.paused.load(Ordering::Relaxed) {
                    continue;
                }
                let seq = clipboard_sequence_number();
                if seq != state_clip.last_seq.load(Ordering::Relaxed)
                    && state_clip.process_current_clipboard(&app_clip)
                {
                    state_clip
                        .last_seq
                        .store(clipboard_sequence_number(), Ordering::Relaxed);
                }
            });

            let app_cat = app.handle().clone();
            let state_cat = Arc::clone(&state_setup);
            thread::spawn(move || {
                let fresh = list_agy_models();
                let current_model = state_cat.model.lock().unwrap().clone();
                let (family, effort) = find_family_and_effort(&fresh, &current_model);
                let resolved = fresh
                    .get(&family)
                    .and_then(|m| m.get(&effort))
                    .cloned()
                    .unwrap_or(current_model);
                *state_cat.catalogue.lock().unwrap() = fresh.clone();
                *state_cat.model.lock().unwrap() = resolved;
                *state_cat.effort.lock().unwrap() = effort.clone();
                let _ = app_cat.emit(
                    "qcm:catalogue",
                    CatalogueEvent {
                        catalogue: fresh,
                        family,
                        effort,
                    },
                );
            });

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
