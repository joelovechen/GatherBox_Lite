use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Manager, WindowEvent};

struct BackendState(Mutex<Option<Child>>);

#[derive(Clone, serde::Serialize)]
struct ServerInfo {
    port: u16,
    token: String,
    status: String,
}

struct ServerInfoState(Mutex<ServerInfo>);

fn generate_auth_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    use std::time::{SystemTime, UNIX_EPOCH};

    let now_nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id();

    let mut h1 = RandomState::new().build_hasher();
    h1.write_u128(now_nanos);
    h1.write_u32(pid);
    let part1 = h1.finish();

    let mut h2 = RandomState::new().build_hasher();
    h2.write_u64(part1 ^ 0x9E37_79B9_7F4A_7C15);
    h2.write_u128(now_nanos.wrapping_mul(6364136223846793005));
    let part2 = h2.finish();

    format!("{:016x}{:016x}", part1, part2)
}

fn pick_available_port(preferred: u16) -> u16 {
    if let Ok(listener) = TcpListener::bind(("127.0.0.1", preferred)) {
        if let Ok(addr) = listener.local_addr() {
            return addr.port();
        }
    }
    if let Ok(listener) = TcpListener::bind(("127.0.0.1", 0)) {
        if let Ok(addr) = listener.local_addr() {
            return addr.port();
        }
    }
    preferred
}

#[cfg(windows)]
mod webview2_guard {
    use std::os::windows::process::CommandExt;
    use std::path::PathBuf;
    use std::process::Command;

    const CREATE_NO_WINDOW: u32 = 0x08000000;
    const WEBVIEW2_CLIENT_GUID: &str = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";

    pub fn is_webview2_installed() -> bool {
        let reg_paths = [
            format!(
                r"HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{}",
                WEBVIEW2_CLIENT_GUID
            ),
            format!(
                r"HKLM\SOFTWARE\Microsoft\EdgeUpdate\Clients\{}",
                WEBVIEW2_CLIENT_GUID
            ),
            format!(
                r"HKCU\Software\Microsoft\EdgeUpdate\Clients\{}",
                WEBVIEW2_CLIENT_GUID
            ),
        ];

        for key in &reg_paths {
            if let Ok(output) = Command::new("reg")
                .args(["query", key, "/v", "pv"])
                .creation_flags(CREATE_NO_WINDOW)
                .output()
            {
                if output.status.success() {
                    let stdout = String::from_utf8_lossy(&output.stdout);
                    for line in stdout.lines() {
                        if line.contains("pv") && line.contains("REG_SZ") {
                            let ver = line.split("REG_SZ").nth(1).unwrap_or("").trim();
                            if !ver.is_empty() && ver != "0.0.0.0" {
                                return true;
                            }
                        }
                    }
                }
            }
        }
        false
    }

    fn find_bootstrapper() -> Option<PathBuf> {
        let mut candidates = Vec::new();
        if let Ok(exe_path) = std::env::current_exe() {
            if let Some(parent) = exe_path.parent() {
                candidates.push(parent.join("resources").join("MicrosoftEdgeWebview2Setup.exe"));
                candidates.push(parent.join("MicrosoftEdgeWebview2Setup.exe"));
            }
        }
        candidates.push(PathBuf::from("resources/MicrosoftEdgeWebview2Setup.exe"));
        candidates.push(PathBuf::from("MicrosoftEdgeWebview2Setup.exe"));
        candidates.push(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("resources")
                .join("MicrosoftEdgeWebview2Setup.exe"),
        );

        candidates.into_iter().find(|p| p.exists())
    }

    pub fn ensure_webview2_installed() {
        if is_webview2_installed() {
            return;
        }
        if let Some(installer) = find_bootstrapper() {
            // 优先静默在线引导安装 (/silent /install)
            let status = Command::new(&installer)
                .args(["/silent", "/install"])
                .status();
            if status.map(|s| s.success()).unwrap_or(false) && is_webview2_installed() {
                return;
            }
            // 若静默模式被拒绝或失败，回退为常规带进度条引导安装 (/install)
            let _ = Command::new(&installer).arg("/install").status();
        }
    }
}

#[cfg(windows)]
mod job_object {
    use std::ffi::c_void;
    use std::mem::size_of;

    type HANDLE = *mut c_void;
    type BOOL = i32;
    type DWORD = u32;

    const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: DWORD = 0x2000;
    const JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: i32 = 9;

    #[repr(C)]
    struct IO_COUNTERS {
        read_operation_count: u64,
        write_operation_count: u64,
        other_operation_count: u64,
        read_transfer_count: u64,
        write_transfer_count: u64,
        other_transfer_count: u64,
    }

    #[repr(C)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        per_process_user_time_limit: i64,
        per_job_user_time_limit: i64,
        limit_flags: DWORD,
        minimum_working_set_size: usize,
        maximum_working_set_size: usize,
        active_process_limit: DWORD,
        affinity: usize,
        priority_class: DWORD,
        scheduling_class: DWORD,
    }

    #[repr(C)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        basic_limit_information: JOBOBJECT_BASIC_LIMIT_INFORMATION,
        io_info: IO_COUNTERS,
        process_memory_limit: usize,
        job_memory_limit: usize,
        peak_process_memory_limit: usize,
        peak_job_memory_limit: usize,
    }

    extern "system" {
        fn CreateJobObjectW(lpJobAttributes: *mut c_void, lpName: *const u16) -> HANDLE;
        fn SetInformationJobObject(
            hJob: HANDLE,
            JobObjectInformationClass: i32,
            lpJobObjectInformation: *const c_void,
            cbJobObjectInformationLength: DWORD,
        ) -> BOOL;
        fn AssignProcessToJobObject(hJob: HANDLE, hProcess: HANDLE) -> BOOL;
        fn GetCurrentProcess() -> HANDLE;
    }

    pub fn init_kill_on_close_job() -> Option<HANDLE> {
        unsafe {
            let job = CreateJobObjectW(std::ptr::null_mut(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.basic_limit_information.limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let res = SetInformationJobObject(
                job,
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                &info as *const _ as *const c_void,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as DWORD,
            );
            if res == 0 {
                return None;
            }
            let current_proc = GetCurrentProcess();
            if AssignProcessToJobObject(job, current_proc) == 0 {
                return None;
            }
            Some(job)
        }
    }
}

enum BackendLaunch {
    Binary { exe: PathBuf, app_root: PathBuf },
    PythonScript { python: PathBuf, script: PathBuf, work_dir: PathBuf },
}

fn detect_backend() -> Option<BackendLaunch> {
    if let Ok(exe_path) = std::env::current_exe() {
        if let Some(parent) = exe_path.parent() {
            // 1. Nuitka --standalone 目录布局: <app_root>/bili_backend/bili_backend.exe
            let nuitka_dir_exe = parent.join("bili_backend").join("bili_backend.exe");
            if nuitka_dir_exe.exists() {
                return Some(BackendLaunch::Binary {
                    exe: nuitka_dir_exe,
                    app_root: parent.to_path_buf(),
                });
            }
            // 2. Nuitka 原始 .dist 目录布局: <app_root>/bili_backend.dist/bili_backend.exe
            let nuitka_dist_exe = parent.join("bili_backend.dist").join("bili_backend.exe");
            if nuitka_dist_exe.exists() {
                return Some(BackendLaunch::Binary {
                    exe: nuitka_dist_exe,
                    app_root: parent.to_path_buf(),
                });
            }
            // 3. 同级目录单可执行文件
            let candidate = parent.join("bili_backend.exe");
            if candidate.exists() {
                return Some(BackendLaunch::Binary {
                    exe: candidate,
                    app_root: parent.to_path_buf(),
                });
            }
            let candidate_msvc = parent.join("bili_backend-x86_64-pc-windows-msvc.exe");
            if candidate_msvc.exists() {
                return Some(BackendLaunch::Binary {
                    exe: candidate_msvc,
                    app_root: parent.to_path_buf(),
                });
            }
        }
    }

    let local_nuitka = PathBuf::from("bili_backend/bili_backend.exe");
    if local_nuitka.exists() {
        let app_root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        return Some(BackendLaunch::Binary {
            exe: local_nuitka,
            app_root,
        });
    }

    let local = PathBuf::from("bili_backend.exe");
    if local.exists() {
        let app_root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        return Some(BackendLaunch::Binary {
            exe: local,
            app_root,
        });
    }

    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()?
        .parent()?
        .parent()?
        .to_path_buf();
    let py = if cfg!(windows) {
        project_root.join("dev/.venv/Scripts/python.exe")
    } else {
        project_root.join("dev/.venv/bin/python")
    };
    let script = project_root.join("dev/BilibiliApis-master/server.py");
    let work_dir = project_root.join("dev/BilibiliApis-master");
    if py.exists() && script.exists() {
        return Some(BackendLaunch::PythonScript {
            python: py,
            script,
            work_dir,
        });
    }

    None
}

#[tauri::command]
fn win_minimize(window: tauri::WebviewWindow) {
    let _ = window.minimize();
}

#[tauri::command]
fn win_toggle_maximize(window: tauri::WebviewWindow) {
    if let Ok(is_max) = window.is_maximized() {
        if is_max {
            let _ = window.unmaximize();
        } else {
            let _ = window.maximize();
        }
    }
}

#[tauri::command]
fn win_close(window: tauri::WebviewWindow) {
    let _ = window.close();
}

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

#[tauri::command]
fn get_server_info(state: tauri::State<ServerInfoState>) -> ServerInfo {
    if let Ok(lock) = state.0.lock() {
        return lock.clone();
    }
    ServerInfo {
        port: 18888,
        token: String::new(),
        status: "UNKNOWN".to_string(),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(windows)]
    {
        // 1. 初始化 Windows Job Object: 当主进程退出时内核强制回收所有子进程树
        let _ = job_object::init_kill_on_close_job();
        // 2. 检测 WebView2 Runtime 是否存在，若缺失则自动调用内置 MicrosoftEdgeWebview2Setup.exe 引导安装
        webview2_guard::ensure_webview2_installed();
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let main_pid = std::process::id();
            let port = pick_available_port(18888);
            let token = std::env::var("BILI_AUTH_TOKEN")
                .ok()
                .map(|v| v.trim().to_string())
                .filter(|v| !v.is_empty())
                .unwrap_or_else(generate_auth_token);

            app.manage(BackendState(Mutex::new(None)));
            app.manage(ServerInfoState(Mutex::new(ServerInfo {
                port,
                token: token.clone(),
                status: "STARTING".to_string(),
            })));

            // 查找并启动后台 Python 服务进程 (Nuitka 原生二进制或源码虚拟环境)
            if let Some(launch) = detect_backend() {
                let is_binary = matches!(launch, BackendLaunch::Binary { .. });
                let mut cmd = match launch {
                    BackendLaunch::Binary { exe, app_root } => {
                        let default_dir = PathBuf::from(".");
                        let bin_dir = exe.parent().unwrap_or(&default_dir);
                        let data_dir = std::env::var("BILI_DATA_DIR")
                            .map(PathBuf::from)
                            .unwrap_or_else(|_| app_root.join("download"));
                        let mut c = Command::new(&exe);
                        c.arg("--parent-pid").arg(main_pid.to_string());
                        c.arg("--port").arg(port.to_string());
                        c.arg("--data-dir").arg(&data_dir);
                        if !token.is_empty() {
                            c.arg("--token").arg(&token);
                        }
                        c.current_dir(bin_dir);
                        c
                    }
                    BackendLaunch::PythonScript {
                        python,
                        script,
                        work_dir,
                    } => {
                        let mut c = Command::new(&python);
                        c.arg(&script);
                        c.arg("--parent-pid").arg(main_pid.to_string());
                        c.arg("--port").arg(port.to_string());
                        if !token.is_empty() {
                            c.arg("--token").arg(&token);
                        }
                        c.current_dir(&work_dir);
                        c
                    }
                };

                cmd.env("PYTHONIOENCODING", "utf-8");
                cmd.env("PYTHONUNBUFFERED", "1");
                cmd.env("BILI_AUTH_TOKEN", &token);

                #[cfg(windows)]
                {
                    use std::os::windows::process::CommandExt;
                    const CREATE_NEW_CONSOLE: u32 = 0x00000010;
                    const CREATE_NO_WINDOW: u32 = 0x08000000;

                    let show_console =
                        std::env::var("BILI_SHOW_CONSOLE").unwrap_or_default() == "1";
                    let hide_console =
                        std::env::var("BILI_HIDE_CONSOLE").unwrap_or_default() == "1";
                    let new_console =
                        std::env::var("BILI_NEW_CONSOLE").unwrap_or_default() == "1";

                    if new_console {
                        cmd.creation_flags(CREATE_NEW_CONSOLE);
                    } else if hide_console || (is_binary && !show_console) {
                        cmd.creation_flags(CREATE_NO_WINDOW);
                    } else {
                        cmd.stdout(std::process::Stdio::inherit());
                        cmd.stderr(std::process::Stdio::inherit());
                    }
                }

                if let Ok(child) = cmd.spawn() {
                    let state = app.state::<BackendState>();
                    if let Ok(mut lock) = state.0.lock() {
                        *lock = Some(child);
                    };
                    let info_state = app.state::<ServerInfoState>();
                    if let Ok(mut info_lock) = info_state.0.lock() {
                        info_lock.status = "READY".to_string();
                    };
                }
            }

            if let Some(main_win) = app.get_webview_window("main") {
                if let Some(icon) = app.default_window_icon().cloned() {
                    let _ = main_win.set_icon(icon);
                }
                if let Ok(Some(monitor)) = main_win.current_monitor() {
                    let scale = monitor.scale_factor();
                    let logical_h = (monitor.size().height as f64 / scale).round();
                    let logical_w = (monitor.size().width as f64 / scale).round();
                    if logical_h < 840.0 || logical_w < 1280.0 {
                        let target_w = (logical_w * 0.9).min(1200.0).max(980.0);
                        let target_h = (logical_h * 0.88).min(760.0).max(640.0);
                        let _ = main_win.set_size(tauri::Size::Logical(tauri::LogicalSize {
                            width: target_w,
                            height: target_h,
                        }));
                    }
                }
                let _ = main_win.set_min_size(Some(tauri::Size::Logical(tauri::LogicalSize {
                    width: 980.0,
                    height: 640.0,
                })));
                let _ = main_win.center();

                if std::env::var("BILI_OPEN_DEVTOOLS").unwrap_or_default() == "1" {
                    main_win.open_devtools();
                }
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } | WindowEvent::Destroyed = event {
                let app = window.app_handle();
                if let Some(state) = app.try_state::<BackendState>() {
                    if let Ok(mut lock) = state.0.lock() {
                        if let Some(mut child) = lock.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            greet,
            get_server_info,
            win_minimize,
            win_toggle_maximize,
            win_close
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
