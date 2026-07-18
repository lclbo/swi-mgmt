#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const API_HOST: &str = "127.0.0.1";
const API_PORT: u16 = 18742;

/// Sidecar ownership for this app instance.
///
/// `shutdown_token` is set only when *this* process spawned the API. It is a
/// one-time secret shared with that child via `--shutdown-token`. On quit we
/// ask the child to exit itself (`POST /api/shutdown` + token). We never call
/// `CommandChild::kill()`, and we never shut down a reused external API
/// (token stays `None` in that case).
struct ApiSidecar {
    shutdown_token: Mutex<Option<String>>,
    /// Kept so the spawn handle is not dropped mid-run; unused for kill.
    #[allow(dead_code)]
    child: Mutex<Option<CommandChild>>,
    /// After graceful sidecar shutdown finishes, allow the real app exit.
    exit_cleanup_done: AtomicBool,
    /// Ensures ExitRequested / Exit only start shutdown once.
    shutdown_started: AtomicBool,
}

fn api_port_open() -> bool {
    TcpStream::connect((API_HOST, API_PORT)).is_ok()
}

/// True only when our HTTP API answers /api/health (not merely something bound).
fn api_healthy() -> bool {
    let req = format!(
        "GET /api/health HTTP/1.1\r\n\
         Host: {API_HOST}:{API_PORT}\r\n\
         Connection: close\r\n\
         \r\n"
    );
    let Ok(mut stream) = TcpStream::connect((API_HOST, API_PORT)) else {
        return false;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let _ = stream.set_read_timeout(Some(Duration::from_millis(400)));
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 256];
    let n = stream.read(&mut buf).unwrap_or(0);
    let head = String::from_utf8_lossy(&buf[..n]);
    (head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200"))
        && head.contains("\"status\"")
}

/// Opaque secret for parent↔sidecar shutdown auth (not a user credential).
fn new_shutdown_token() -> String {
    let mut bytes = [0u8; 16];
    #[cfg(unix)]
    {
        if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
            let _ = f.read_exact(&mut bytes);
        }
    }
    if bytes.iter().all(|&b| b == 0) {
        use std::time::{SystemTime, UNIX_EPOCH};
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let pid = std::process::id() as u128;
        let mixed = nanos ^ (pid << 64) ^ (pid << 32);
        bytes = mixed.to_le_bytes();
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn ensure_sidecar_state(
    app: &tauri::AppHandle,
    child: Option<CommandChild>,
    shutdown_token: Option<String>,
) {
    if app.try_state::<ApiSidecar>().is_none() {
        app.manage(ApiSidecar {
            shutdown_token: Mutex::new(shutdown_token),
            child: Mutex::new(child),
            exit_cleanup_done: AtomicBool::new(false),
            shutdown_started: AtomicBool::new(false),
        });
        return;
    }
    if let Some(state) = app.try_state::<ApiSidecar>() {
        if let Some(token) = shutdown_token {
            *state
                .shutdown_token
                .lock()
                .unwrap_or_else(|e| e.into_inner()) = Some(token);
        }
        if let Some(child) = child {
            *state.child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
        }
    }
}

/// Title bar + full-window overlay so quit feedback is visible while we wait.
fn show_shutting_down_ui(app: &tauri::AppHandle) {
    let Some(window) = app.get_webview_window("main") else {
        return;
    };
    let _ = window.set_title("SWI-MGMT — Shutting down…");
    let _ = window.eval(
        r#"(function () {
  if (document.getElementById("swi-shutdown-overlay")) return;
  var el = document.createElement("div");
  el.id = "swi-shutdown-overlay";
  el.setAttribute("role", "status");
  el.setAttribute("aria-live", "polite");
  el.style.cssText = [
    "position:fixed",
    "inset:0",
    "z-index:99999",
    "display:flex",
    "flex-direction:column",
    "align-items:center",
    "justify-content:center",
    "gap:10px",
    "background:color-mix(in srgb, var(--bg, #0f0f1a) 88%, transparent)",
    "color:var(--text-strong, #f1f5f9)",
    "font:600 1.15rem/1.4 system-ui,sans-serif",
    "backdrop-filter:blur(2px)",
    "pointer-events:all",
    "cursor:wait"
  ].join(";");
  var title = document.createElement("div");
  title.textContent = "Shutting down…";
  var sub = document.createElement("div");
  sub.textContent = "Stopping the local API";
  sub.style.cssText = "font-weight:500;font-size:0.9rem;color:var(--muted,#94a3b8)";
  el.appendChild(title);
  el.appendChild(sub);
  document.documentElement.appendChild(el);
})()"#,
    );
}

fn take_owned_shutdown_token(app: &tauri::AppHandle) -> Option<String> {
    let state = app.try_state::<ApiSidecar>()?;
    let token = state
        .shutdown_token
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take();
    token
}

/// Ask the sidecar we started to exit itself. No kill(); no effect without token.
fn run_owned_sidecar_shutdown(token: &str) {
    eprintln!("[api] quit: requesting graceful sidecar shutdown");
    let ok = post_shutdown(token);
    if !ok {
        eprintln!("[api] quit: shutdown request failed; leaving process alone (no kill)");
        return;
    }

    // Soft wait only (option C): never escalate to kill if it does not exit.
    let deadline = std::time::Instant::now() + Duration::from_secs(2);
    while std::time::Instant::now() < deadline {
        if !api_port_open() {
            eprintln!("[api] quit: sidecar stopped");
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    eprintln!("[api] quit: sidecar still listening after wait; leaving it alone");
}

fn post_shutdown(token: &str) -> bool {
    let req = format!(
        "POST /api/shutdown HTTP/1.1\r\n\
         Host: {API_HOST}:{API_PORT}\r\n\
         X-SWI-Shutdown-Token: {token}\r\n\
         Content-Length: 0\r\n\
         Connection: close\r\n\
         \r\n"
    );
    let Ok(mut stream) = TcpStream::connect((API_HOST, API_PORT)) else {
        return false;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(800)));
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    let n = stream.read(&mut buf).unwrap_or(0);
    let head = String::from_utf8_lossy(&buf[..n]);
    head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
}

/// Drop Gatekeeper quarantine on the MacOS binaries folder so a freshly copied
/// .app can spawn its PyInstaller sidecar on first launch.
#[cfg(target_os = "macos")]
fn clear_quarantine(path: &Path) {
    let _ = std::process::Command::new("xattr")
        .args(["-dr", "com.apple.quarantine"])
        .arg(path)
        .status();
}

#[cfg(target_os = "macos")]
fn clear_macos_quarantine() {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            clear_quarantine(dir);
        }
    }
}

#[cfg(not(target_os = "macos"))]
fn clear_macos_quarantine() {}

fn spawn_backend(app: &tauri::AppHandle) -> Result<(), String> {
    if std::env::var("SWI_MGMT_SKIP_BACKEND").is_ok() {
        ensure_sidecar_state(app, None, None);
        return Ok(());
    }

    // Already running (orphaned sidecar, previous app, or `npm run dev`).
    // Do not take ownership — no shutdown token — so quit will not stop it.
    if api_healthy() {
        eprintln!("[api] reusing healthy server on {API_HOST}:{API_PORT}");
        ensure_sidecar_state(app, None, None);
        return Ok(());
    }
    if api_port_open() {
        eprintln!(
            "[api] port {API_PORT} is open but /api/health failed; \
             not reusing — will try to spawn (bind may fail)"
        );
    }

    clear_macos_quarantine();

    match app.shell().sidecar("swi-mgmt-api") {
        Ok(command) => {
            if api_healthy() {
                eprintln!("[api] reusing healthy server on {API_HOST}:{API_PORT}");
                ensure_sidecar_state(app, None, None);
                return Ok(());
            }

            // Parent-generated secret: only this spawn gets it; only this app
            // keeps a copy. Proves quit-time shutdown targets *our* child.
            let shutdown_token = new_shutdown_token();

            // Run outside ~/Documents so macOS "Documents folder" TCC is not
            // tripped by a relative cwd when the .app itself lives under Documents.
            let (mut rx, child) = command
                .current_dir(std::env::temp_dir())
                .args([
                    "--host",
                    API_HOST,
                    "--port",
                    &API_PORT.to_string(),
                    "--shutdown-token",
                    &shutdown_token,
                ])
                .spawn()
                .map_err(|e| format!("failed to spawn swi-mgmt-api sidecar: {e}"))?;

            // Drain sidecar output so pipes cannot fill and stall the process.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            eprintln!("[api] {}", String::from_utf8_lossy(&line));
                        }
                        CommandEvent::Stderr(line) => {
                            let text = String::from_utf8_lossy(&line);
                            if text.contains("address already in use") {
                                eprintln!(
                                    "[api] port {API_PORT} busy — will use the process that holds it"
                                );
                            } else {
                                eprintln!("[api] {text}");
                            }
                        }
                        CommandEvent::Error(err) => {
                            eprintln!("[api] error: {err}");
                        }
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[api] exited: code={:?}", payload.code);
                        }
                        _ => {}
                    }
                }
            });

            ensure_sidecar_state(app, Some(child), Some(shutdown_token));

            // PyInstaller onefile cold-start can take several seconds; never fail setup.
            let ready = {
                let deadline = std::time::Instant::now() + Duration::from_secs(15);
                let mut ok = false;
                while std::time::Instant::now() < deadline {
                    if api_healthy() {
                        ok = true;
                        break;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
                ok
            };
            if ready {
                eprintln!("[api] sidecar healthy on {API_HOST}:{API_PORT}");
            } else {
                eprintln!(
                    "[api] not healthy yet on {API_HOST}:{API_PORT}; UI will keep retrying"
                );
            }
            Ok(())
        }
        Err(err) => {
            ensure_sidecar_state(app, None, None);
            Err(format!("sidecar not available: {err}"))
        }
    }
}

fn begin_owned_sidecar_shutdown(app: &tauri::AppHandle, prevent_exit: Option<&tauri::ExitRequestApi>) {
    let Some(state) = app.try_state::<ApiSidecar>() else {
        return;
    };
    if state.exit_cleanup_done.load(Ordering::SeqCst) {
        return;
    }
    if state
        .shutdown_started
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        if let Some(api) = prevent_exit {
            api.prevent_exit();
        }
        return;
    }

    let Some(token) = take_owned_shutdown_token(app) else {
        eprintln!("[api] quit: no owned sidecar token; not requesting shutdown");
        state.shutdown_started.store(false, Ordering::SeqCst);
        return;
    };

    if let Some(api) = prevent_exit {
        // Keep the window up so the user can see "Shutting down…" while the
        // sidecar stops itself (work runs off the UI thread so overlay can paint).
        api.prevent_exit();
        show_shutting_down_ui(app);
    }

    let app_handle = app.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(80));
        run_owned_sidecar_shutdown(&token);
        if let Some(state) = app_handle.try_state::<ApiSidecar>() {
            state.exit_cleanup_done.store(true, Ordering::SeqCst);
        }
        app_handle.exit(0);
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Never propagate Err out of setup on macOS — that aborts the process
            // with panic_cannot_unwind inside applicationDidFinishLaunching.
            if let Err(err) = spawn_backend(app.handle()) {
                eprintln!("[api] backend start warning: {err}");
                ensure_sidecar_state(app.handle(), None, None);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| match event {
            tauri::RunEvent::ExitRequested { api, .. } => {
                begin_owned_sidecar_shutdown(app, Some(&api));
            }
            tauri::RunEvent::Exit => {
                // Last chance if ExitRequested was skipped (e.g. abrupt teardown).
                if let Some(state) = app.try_state::<ApiSidecar>() {
                    if state.exit_cleanup_done.load(Ordering::SeqCst) {
                        return;
                    }
                }
                if let Some(token) = take_owned_shutdown_token(app) {
                    eprintln!("[api] Exit: last-chance graceful sidecar shutdown");
                    run_owned_sidecar_shutdown(&token);
                }
            }
            _ => {}
        });
}
