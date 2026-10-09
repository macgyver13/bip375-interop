use eframe::egui;
use serde_json::Value;
use std::ffi::OsStr;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use url::Url;

const PROFILES: [(&str, &str); 5] = [
    ("BIP-375 baseline", "baseline/interop.yaml"),
    ("BIP-375 + MuSig2 baseline", "baseline-musig2/interop.yaml"),
    ("Live development", "interop.yaml"),
    ("BIP-375 fetched baseline", "baseline/interop.fetched.yaml"),
    (
        "BIP-375 + MuSig2 fetched baseline",
        "baseline-musig2/interop.fetched.yaml",
    ),
];
const SETUP_GUIDE: &str = "docs/runbook.md";
const LIVE_PROFILE: usize = 2;
const COMPARISON_BASELINES: [(&str, &str); 2] = [
    ("BIP-375 baseline", "baseline/interop.lock"),
    ("BIP-375 + MuSig2 baseline", "baseline-musig2/interop.lock"),
];
const PROJECTS: [&str; 7] = [
    "harness",
    "coldcard",
    "jade",
    "seedsigner",
    "bitsaga-seedsigner",
    "caravan",
    "spdk",
];

#[derive(Clone, Copy, PartialEq)]
enum Action {
    Compare,
    Doctor,
    Fetch,
    Preview,
    Check,
    PinPreview,
    Pin,
}

enum Event {
    Comparison(Result<Value, String>),
    Progress(Value),
    Finished(Action, i32, String, String),
}

struct App {
    root: PathBuf,
    profile: usize,
    project: usize,
    allow_dirty: bool,
    running: Option<Action>,
    receiver: Receiver<Event>,
    sender: Sender<Event>,
    message: String,
    progress: String,
    completed: usize,
    total: usize,
    doctor: Option<Value>,
    selection: Option<Value>,
    report: Option<Value>,
    summary: Option<Value>,
    pin_changes: Option<Value>,
    pin_reviewed: bool,
    comparison_baseline: usize,
    comparison: Option<Value>,
    comparison_error: Option<String>,
    reviewed_checkout: Option<String>,
}

impl App {
    fn new(root: PathBuf) -> Self {
        let (sender, receiver) = mpsc::channel();
        Self {
            root,
            profile: 0,
            project: 0,
            allow_dirty: false,
            running: None,
            receiver,
            sender,
            message: "Choose a profile, inspect the plan, then run a check.".into(),
            progress: String::new(),
            completed: 0,
            total: 0,
            doctor: None,
            selection: None,
            report: None,
            summary: None,
            pin_changes: None,
            pin_reviewed: false,
            comparison_baseline: 0,
            comparison: None,
            comparison_error: None,
            reviewed_checkout: None,
        }
    }

    fn setup_guide_link(&mut self, ui: &mut egui::Ui) {
        if ui
            .link("Setup prerequisites")
            .on_hover_text(format!("{SETUP_GUIDE}, \"Setting up a baseline\""))
            .clicked()
        {
            if let Err(error) = open_setup_guide(&self.root) {
                self.message = format!("Could not open {SETUP_GUIDE}: {error}");
            }
        }
    }

    fn start(&mut self, action: Action) {
        if self.running.is_some() {
            return;
        }
        self.running = Some(action);
        match action {
            Action::Compare => self.comparison_error = None,
            Action::Doctor => self.doctor = None,
            Action::Fetch => {}
            Action::Preview => self.selection = None,
            Action::Check => {
                self.summary = None;
                self.report = None;
            }
            Action::PinPreview => {
                self.pin_reviewed = false;
                self.pin_changes = None;
            }
            Action::Pin => {}
        }
        self.message.clear();
        self.progress.clear();
        self.completed = 0;
        self.total = 0;
        let root = self.root.clone();
        let config = PROFILES[self.profile].1.to_string();
        let project = PROJECTS[self.project].to_string();
        let allow_dirty = self.allow_dirty;
        let baseline_lock = (self.profile == LIVE_PROFILE)
            .then(|| COMPARISON_BASELINES[self.comparison_baseline].1.to_string());
        let sender = self.sender.clone();
        std::thread::spawn(move || {
            if action == Action::Doctor && baseline_lock.is_some() {
                let comparison = run_cli(
                    &root, &config, &project, allow_dirty, Action::Compare,
                    baseline_lock.as_deref(), sender.clone(),
                ).and_then(|(code, stdout, stderr)| {
                    if code == 0 {
                        serde_json::from_str(&stdout).map_err(|error| error.to_string())
                    } else {
                        Err(stderr.trim().trim_start_matches("error: ").to_string())
                    }
                });
                let _ = sender.send(Event::Comparison(comparison));
            }
            let result = run_cli(
                &root,
                &config,
                &project,
                allow_dirty,
                action,
                baseline_lock.as_deref(),
                sender.clone(),
            );
            let (code, stdout, stderr) = match result {
                Ok(value) => value,
                Err(error) => (2, String::new(), error),
            };
            let _ = sender.send(Event::Finished(action, code, stdout, stderr));
        });
    }

    fn receive(&mut self) {
        while let Ok(event) = self.receiver.try_recv() {
            match event {
                Event::Comparison(result) => match result {
                    Ok(comparison) => self.set_comparison(comparison),
                    Err(error) => {
                        self.comparison = None;
                        self.comparison_error = Some(error);
                    }
                },
                Event::Progress(value) => {
                    let stage = value["stage"].as_str().unwrap_or("");
                    if stage == "baseline-comparison" {
                        if let Some(comparison) = value.get("comparison") {
                            self.set_comparison(comparison.clone());
                        }
                        continue;
                    }
                    let name = value["scenario"]
                        .as_str()
                        .or_else(|| value["architecture"].as_str())
                        .unwrap_or("");
                    if let Some(total) = value["total"].as_u64() {
                        self.total = total as usize;
                    }
                    if stage == "case-done" {
                        self.completed = value["index"].as_u64().unwrap_or(0) as usize;
                    } else if stage == "musig2-start" {
                        // One start event per regtest leg, each finished by one musig2-done.
                        self.total += 1;
                    } else if stage == "musig2-done" {
                        self.completed += 1;
                    }
                    self.progress = match stage {
                        "preflight" => "Checking prerequisites and pinned revisions…".into(),
                        "case-start" => format!("Running {name}"),
                        "case-done" => format!(
                            "Finished {name}: {}",
                            value["status"].as_str().unwrap_or("unknown")
                        ),
                        "musig2-start" => format!("Running MuSig2 regtest: {name}"),
                        "musig2-done" => format!(
                            "MuSig2 regtest {name}: {}",
                            if value["passed"] == true {
                                "PASS"
                            } else {
                                "ISSUE"
                            }
                        ),
                        _ => stage.to_string(),
                    };
                }
                Event::Finished(action, code, stdout, stderr) => {
                    self.running = None;
                    self.finish(action, code, &stdout, &stderr);
                }
            }
        }
    }

    fn finish(&mut self, action: Action, code: i32, stdout: &str, stderr: &str) {
        let parsed = serde_json::from_str::<Value>(stdout).ok();
        if parsed.is_none() || (code != 0 && action != Action::Check) {
            self.message = format!(
                "Could not complete: {}",
                if stderr.trim().is_empty() {
                    stdout.trim()
                } else {
                    stderr.trim().trim_start_matches("error: ")
                }
            );
            if action == Action::Compare {
                self.comparison = None;
                self.comparison_error = Some(self.message.clone());
            }
            return;
        }
        if let Some(comparison) = parsed.as_ref().and_then(|value| value.get("baseline_comparison")) {
            self.set_comparison(comparison.clone());
        }
        match action {
            Action::Compare => {
                self.set_comparison(parsed.unwrap());
                self.message = "Review live checkout differences from the selected baseline.".into();
            }
            Action::Doctor => {
                self.doctor = parsed;
                self.message = "Checkout versions and cleanliness are shown below.".into();
            }
            Action::Fetch => {
                self.message = "Pinned checkouts fetched into this profile's paths. Build their prerequisites, then check setup.".into();
            }
            Action::Preview => {
                self.selection = parsed;
                self.message = "Review the selected cases and blocked items before running.".into();
            }
            Action::Check => {
                self.selection = None;
                self.summary = parsed;
                self.report = self
                    .summary
                    .as_ref()
                    .and_then(|v| v["manifest"].as_str())
                    .and_then(|path| std::fs::read_to_string(path).ok())
                    .and_then(|text| serde_json::from_str(&text).ok());
                self.message = if code == 0 {
                    "Check finished. Review coverage and findings below.".into()
                } else {
                    format!(
                        "Check found an issue (exit {code}). Review the cases and reasons below."
                    )
                };
            }
            Action::PinPreview => {
                self.pin_changes = parsed;
                self.pin_reviewed = self.pin_changes.is_some();
                self.message = "Review these proposed revisions before updating the lock.".into();
            }
            Action::Pin => {
                self.pin_reviewed = false;
                self.pin_changes = None;
                self.message = "Pins updated. Run the full check, review changed results, then update expectations together with the lock.".into();
            }
        }
    }

    fn set_comparison(&mut self, comparison: Value) {
        self.comparison = Some(comparison);
        self.comparison_error = None;
    }

    fn checkout_status_ui(&self, ui: &mut egui::Ui) {
        if self.profile == LIVE_PROFILE {
            return;
        }
        if let Some(doctor) = &self.doctor {
            ui.heading("Checkout status");
            if let Some(items) = doctor.as_array() {
                for item in items {
                    ui.label(format!("{}: {}{}", item["name"].as_str().unwrap_or("?"),
                        item["revision"].as_str().unwrap_or("?"),
                        if item["dirty"] == true { " (uncommitted changes)" } else { "" }));
                }
            }
        }
    }

    fn comparison_ui(&mut self, ui: &mut egui::Ui, missing_live_profile: bool) {
        ui.heading("Live checkouts vs. baseline");
        let ready = self.running.is_none() && !missing_live_profile;
        ui.add_enabled_ui(self.running.is_none(), |ui| {
            ui.horizontal_wrapped(|ui| {
                ui.label("Compare with");
                egui::ComboBox::from_id_salt("comparison-baseline")
                    .selected_text(COMPARISON_BASELINES[self.comparison_baseline].0)
                    .show_ui(ui, |ui| {
                        for (index, (name, _)) in COMPARISON_BASELINES.iter().enumerate() {
                            if ui.selectable_value(&mut self.comparison_baseline, index, *name).changed() {
                                self.comparison = None;
                                self.comparison_error = None;
                                self.reviewed_checkout = None;
                            }
                        }
                    });
                if ui.add_enabled(ready, egui::Button::new("Compare with baseline")).clicked() {
                    self.start(Action::Compare);
                }
            });
        });
        ui.small("Comparison uses live checkout paths. The run's scenarios and expectations still come from Live development.");
        if let Some(error) = &self.comparison_error {
            ui.colored_label(egui::Color32::YELLOW, error);
        }
        if let Some(comparison) = &self.comparison {
            if let Some(rows) = comparison["checkouts"].as_array() {
                let differing = rows.iter().filter(|row| {
                    row["baseline_revision"].is_string() && row["revision"].is_string()
                        && row["baseline_revision"] != row["revision"]
                }).count();
                let dirty = rows.iter().filter(|row| row["dirty"] == true).count();
                let unpinned = rows.iter().filter(|row| row["status"] == "unpinned").count();
                let unavailable = rows.iter().filter(|row| {
                    row["status"] == "missing" || row["status"] == "error"
                }).count();
                ui.label(format!("{differing} revision differences · {dirty} with uncommitted changes · {unpinned} without a pin · {unavailable} unavailable"));
                if let Some(tested) = self.summary.as_ref().and_then(|value| value.get("baseline_comparison")) {
                    if comparison_state(comparison) != comparison_state(tested) {
                        ui.colored_label(egui::Color32::YELLOW,
                            "This comparison differs from the snapshot recorded for the verification below.");
                    } else {
                        ui.small("This comparison matches the checkout state recorded for the verification below.");
                    }
                }
                egui::Grid::new("baseline-comparison").striped(true).max_col_width(190.0).show(ui, |ui| {
                    ui.strong("Codebase");
                    ui.strong("Locked commit");
                    ui.strong("Live commit");
                    ui.strong("Difference");
                    ui.label("");
                    ui.end_row();
                    for row in rows {
                        let name = row["name"].as_str().unwrap_or("?");
                        ui.label(name);
                        ui.monospace(short_revision(&row["baseline_revision"]))
                            .on_hover_text(row["baseline_revision"].as_str().unwrap_or("No baseline pin"));
                        ui.monospace(short_revision(&row["revision"]))
                            .on_hover_text(row["revision"].as_str().unwrap_or("Unavailable"));
                        ui.label(comparison_status(row));
                        if row["status"] != "matches" || row["dirty"] == true {
                            let selected = self.reviewed_checkout.as_deref() == Some(name);
                            if ui.selectable_label(selected, "Review delta").clicked() {
                                self.reviewed_checkout = (!selected).then(|| name.to_string());
                            }
                        } else {
                            ui.label("");
                        }
                        ui.end_row();
                    }
                });
                if let Some(row) = rows.iter().find(|row| row["name"].as_str() == self.reviewed_checkout.as_deref()) {
                    comparison_detail(ui, row);
                }
            }
            ui.small("Review differences before promoting committed changes into a baseline. Pins cannot capture uncommitted changes.");
        } else if self.comparison_error.is_none() {
            ui.label("Compare with baseline or check setup to inspect live checkout differences.");
        }
        let project = self.comparison.as_ref().and_then(affected_project);
        if ui.add_enabled(ready && project.is_some(), egui::Button::new("Preview affected cases")).clicked() {
            let project = project.unwrap();
            if self.project != project {
                self.project = project;
                self.summary = None;
                self.report = None;
            }
            self.start(Action::Preview);
        }
        if let Some(project) = project {
            ui.small(format!("Affected selection: {}{}", PROJECTS[project], if project == 0 { " (all scenarios)" } else { "" }));
        }
        ui.separator();
    }
}

fn short_revision(value: &Value) -> String {
    value.as_str().map(|revision| revision.chars().take(8).collect())
        .unwrap_or_else(|| "—".into())
}

fn comparison_status(row: &Value) -> String {
    let mut status = match row["status"].as_str().unwrap_or("error") {
        "matches" => "Matches".into(),
        "ahead" => format!("{} ahead", row["ahead"]),
        "behind" => format!("{} behind", row["behind"]),
        "diverged" => format!("Diverged: {} ahead, {} behind", row["ahead"], row["behind"]),
        "revision-differs" => "Revision differs".into(),
        "unpinned" => "No baseline pin".into(),
        "missing" => "Missing checkout".into(),
        _ => "Comparison unavailable".into(),
    };
    if row["dirty"] == true {
        status = if row["status"] == "matches" { "Uncommitted changes".into() }
            else { format!("{status} + uncommitted") };
    }
    status
}

fn comparison_detail(ui: &mut egui::Ui, row: &Value) {
    ui.separator();
    ui.strong(format!("{} · {}", row["name"].as_str().unwrap_or("?"), comparison_status(row)));
    if let Some(path) = row["path"].as_str() { ui.small(path); }
    ui.label(format!("Baseline: {}", row["baseline_revision"].as_str().unwrap_or("No baseline pin")));
    ui.label(format!("Live: {}", row["revision"].as_str().unwrap_or("Unavailable")));
    if let Some(error) = row["error"].as_str() {
        ui.colored_label(egui::Color32::YELLOW, error);
    }
    ui.collapsing("Commit history · live commits absent from baseline", |ui| {
        if let Some(commits) = row["commits"].as_array().filter(|commits| !commits.is_empty()) {
            for commit in commits {
                ui.label(format!("{}  {}", short_revision(&commit["revision"]), commit["subject"].as_str().unwrap_or("")));
            }
            if row["ahead"].as_u64().is_some_and(|count| count > commits.len() as u64) {
                ui.small(format!("Showing the latest {} live commits.", commits.len()));
            }
        } else {
            ui.label("No live-only commits recorded.");
        }
    });
    for (key, title) in [("changed_files", "Changed files · baseline to live commit"),
                         ("uncommitted_files", "Uncommitted files · staged, unstaged, and untracked")] {
        ui.collapsing(title, |ui| {
            if let Some(files) = row[key].as_array().filter(|files| !files.is_empty()) {
                for file in files { ui.monospace(file.as_str().unwrap_or("?")); }
            } else {
                ui.label("No files recorded.");
            }
        });
    }
}

fn affected_project(comparison: &Value) -> Option<usize> {
    let rows = comparison["checkouts"].as_array()?;
    let affected: Vec<_> = rows.iter()
        .filter(|row| row["status"] != "matches" || row["dirty"] == true).collect();
    if affected.is_empty() { return None; }
    if affected.len() == 1 {
        let name = affected[0]["name"].as_str()?;
        return Some(PROJECTS.iter().position(|project| *project == name).unwrap_or(0));
    }
    Some(0)
}

fn comparison_state(comparison: &Value) -> Value {
    let states: Vec<_> = comparison["checkouts"].as_array().into_iter().flatten().map(|row| {
        serde_json::json!({"name": row["name"], "path": row["path"], "revision": row["revision"],
            "dirty": row["dirty"], "diff_sha256": row["diff_sha256"], "status": row["status"]})
    }).collect();
    serde_json::json!({"baseline_lock": comparison["baseline_lock"],
        "lock_sha256": comparison["lock_sha256"], "checkouts": states})
}

fn live_profile_source(root: &Path) -> PathBuf {
    let common_dir = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["rev-parse", "--path-format=absolute", "--git-common-dir"])
        .output()
        .ok()
        .filter(|output| output.status.success())
        .and_then(|output| String::from_utf8(output.stdout).ok())
        .map(|path| PathBuf::from(path.trim()));
    if let Some(source) = common_dir
        .and_then(|path| path.parent().map(|parent| parent.join("interop.yaml")))
        .filter(|path| path.is_file())
    {
        return source;
    }
    root.join("config/interop.example.yaml")
}

fn create_live_profile(root: &Path, source: &Path) -> Result<(), String> {
    let contents = std::fs::read(source)
        .map_err(|error| format!("could not read {}: {error}", source.display()))?;
    let destination = root.join("interop.yaml");
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|error| format!("could not create {}: {error}", destination.display()))?;
    file.write_all(&contents)
        .map_err(|error| format!("could not write {}: {error}", destination.display()))
}

/// Errors that mean a checkout is missing, at the wrong commit, or not built yet.
fn needs_setup(message: &str) -> bool {
    ["preflight failed", "missing checkout", ", found "]
        .iter()
        .any(|needle| message.contains(needle))
}

fn open_setup_guide(root: &Path) -> Result<(), String> {
    let path = root.join(SETUP_GUIDE);
    let url = Url::from_file_path(&path)
        .map_err(|()| format!("invalid setup guide path: {}", path.display()))?;
    webbrowser::open(url.as_str()).map_err(|error| error.to_string())
}

fn report_url(path: &Path) -> Result<Url, String> {
    let path = path
        .canonicalize()
        .map_err(|error| format!("could not find HTML report: {error}"))?;
    Url::from_file_path(&path).map_err(|()| format!("invalid HTML report path: {}", path.display()))
}

fn cli_path(
    root: &Path,
    inherited: &OsStr,
    home: Option<&OsStr>,
) -> Result<std::ffi::OsString, String> {
    let mut paths = Vec::new();
    let venv_bin = root.join(".venv/bin");
    if venv_bin.is_dir() {
        paths.push(venv_bin);
    }
    paths.extend(std::env::split_paths(inherited));
    paths.extend([
        PathBuf::from("/opt/homebrew/bin"),
        PathBuf::from("/usr/local/bin"),
    ]);
    if let Some(home) = home {
        paths.push(PathBuf::from(home).join(".local/bin"));
        paths.push(PathBuf::from(home).join(".cargo/bin"));
    }
    paths.dedup();
    std::env::join_paths(paths).map_err(|error| error.to_string())
}

fn check_mode(config: &str, project: &str) -> &'static str {
    if project == "harness" && config.starts_with("baseline-musig2/") {
        "--release"
    } else {
        "--exhaustive"
    }
}

fn run_cli(
    root: &Path,
    config: &str,
    project: &str,
    allow_dirty: bool,
    action: Action,
    baseline_lock: Option<&str>,
    sender: Sender<Event>,
) -> Result<(i32, String, String), String> {
    let local_python = root.join(".venv/bin/python");
    let python = if local_python.is_file() {
        local_python
    } else {
        PathBuf::from("python3")
    };
    let mut command = Command::new(python);
    command
        .current_dir(root)
        .env("PYTHONPATH", root.join("src"))
        .env(
            "PATH",
            cli_path(
                root,
                &std::env::var_os("PATH").unwrap_or_default(),
                std::env::var_os("HOME").as_deref(),
            )?,
        )
        .args(cli_arguments(config, project, allow_dirty, action, baseline_lock));
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("could not start the CLI: {error}"))?;
    let stderr = child.stderr.take().ok_or("missing CLI stderr")?;
    let progress_sender = sender.clone();
    let stderr_reader = std::thread::spawn(move || {
        let mut problems = String::new();
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            if let Ok(event) = serde_json::from_str::<Value>(&line) {
                if event.get("stage").is_some() {
                    let _ = progress_sender.send(Event::Progress(event));
                    continue;
                }
            }
            problems.push_str(&line);
            problems.push('\n');
        }
        problems
    });
    let mut stdout = String::new();
    child
        .stdout
        .take()
        .ok_or("missing CLI stdout")?
        .read_to_string(&mut stdout)
        .map_err(|error| error.to_string())?;
    let status = child.wait().map_err(|error| error.to_string())?;
    let stderr = stderr_reader.join().unwrap_or_default();
    Ok((status.code().unwrap_or(2), stdout, stderr))
}

fn cli_arguments(config: &str, project: &str, allow_dirty: bool, action: Action,
                 baseline_lock: Option<&str>) -> Vec<String> {
    let mut args = vec!["-m", "bip375_interop.cli", "--config", config];
    if allow_dirty && !matches!(action, Action::PinPreview | Action::Pin | Action::Compare) {
        args.push("--allow-dirty");
    }
    match action {
        Action::Compare => args.extend(["compare", "--baseline-lock", baseline_lock.unwrap()]),
        Action::Doctor => args.push("doctor"),
        Action::Fetch => args.extend(["fetch", "--in-place"]),
        Action::Preview | Action::Check => {
            args.extend(["check", "--project", project,
                if action == Action::Preview { "--dry-run" } else { "--progress-json" },
                check_mode(config, project)]);
            if let Some(lock) = baseline_lock { args.extend(["--baseline-lock", lock]); }
        }
        Action::PinPreview => args.extend(["pin", "--dry-run"]),
        Action::Pin => args.push("pin"),
    }
    args.into_iter().map(str::to_string).collect()
}

impl eframe::App for App {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        self.receive();
        if self.running.is_some() {
            ui.ctx()
                .request_repaint_after(std::time::Duration::from_millis(100));
        }
        egui::CentralPanel::default().show(ui, |ui| {
            ui.heading("BIP-375 interoperability");
            ui.label("Choose what changed, review coverage, and see why a case needs attention.");
            ui.separator();
            ui.add_enabled_ui(self.running.is_none(), |ui| {
            ui.horizontal(|ui| {
                ui.label("Profile");
                egui::ComboBox::from_id_salt("profile")
                    .selected_text(PROFILES[self.profile].0)
                    .show_ui(ui, |ui| {
                        for (index, (name, _)) in PROFILES.iter().enumerate() {
                            if ui.selectable_value(&mut self.profile, index, *name).changed() {
                                self.pin_reviewed = false;
                                self.pin_changes = None;
                                self.selection = None;
                                self.doctor = None;
                                self.summary = None;
                                self.report = None;
                                self.comparison = None;
                                self.comparison_error = None;
                                self.reviewed_checkout = None;
                            }
                        }
                    });
                ui.label("Changed code");
                egui::ComboBox::from_id_salt("project")
                    .selected_text(PROJECTS[self.project])
                    .show_ui(ui, |ui| {
                        for (index, name) in PROJECTS.iter().enumerate() {
                            if ui.selectable_value(&mut self.project, index, *name).changed() {
                                self.selection = None;
                                self.summary = None;
                                self.report = None;
                            }
                        }
                    });
            });
            ui.checkbox(&mut self.allow_dirty, "Include uncommitted checkout changes (result is not reproducible)");
            });
            ui.label(if (self.profile == 1 || self.profile == 4) && self.project == 0 {
                "Full verification runs BIP-375/BIP-376 with Caravan, SPDK, and Interop Lab, then both MuSig2 regtest legs."
            } else {
                "This selection runs matching scenarios with independent validators. Use the MuSig2 baseline and all code for the full gate."
            });
            let missing_live_profile = self.profile == LIVE_PROFILE && !self.root.join("interop.yaml").is_file();
            if missing_live_profile {
                let source = live_profile_source(&self.root);
                ui.colored_label(egui::Color32::YELLOW, "Live development needs a local interop.yaml in this checkout.");
                ui.label(format!("Create it from {} and review the checkout paths before running.", source.display()));
                if ui.button("Create live profile").clicked() {
                    self.message = match create_live_profile(&self.root, &source) {
                        Ok(()) => format!("Created interop.yaml from {}. Review its paths, then check setup.", source.display()),
                        Err(error) => format!("Could not create live profile: {error}"),
                    };
                }
            }
            ui.horizontal(|ui| {
                let ready = self.running.is_none() && !missing_live_profile;
                if ui.add_enabled(ready, egui::Button::new("1. Check setup")).clicked() { self.start(Action::Doctor); }
                if ui.add_enabled(ready && self.profile < 2, egui::Button::new("Fetch pinned sources")).clicked() { self.start(Action::Fetch); }
                if ui.add_enabled(ready, egui::Button::new("2. Preview cases")).clicked() { self.start(Action::Preview); }
                if ui.add_enabled(ready, egui::Button::new("3. Verify now")).clicked() { self.start(Action::Check); }
                self.setup_guide_link(ui);
            });
            if self.running.is_some() {
                ui.add(egui::ProgressBar::new(if self.total == 0 { 0.0 } else { self.completed as f32 / self.total as f32 })
                    .show_percentage());
                ui.label(&self.progress);
            }
            ui.label(&self.message);
            if needs_setup(&self.message) {
                ui.horizontal(|ui| {
                    ui.label("Missing or unbuilt checkouts:");
                    self.setup_guide_link(ui);
                });
            }
            ui.separator();
            egui::ScrollArea::vertical().show(ui, |ui| {
                if self.profile == LIVE_PROFILE {
                    self.comparison_ui(ui, missing_live_profile);
                }
                if let Some(selection) = &self.selection {
                    ui.heading("Planned cases");
                    if let Some(cases) = selection["cases"].as_array() {
                        ui.label(format!("{} selected; blocked cases need a prepared PSBT or implementation.", cases.len()));
                        for case in cases {
                            let status = case["status"].as_str().unwrap_or("?");
                            let name = case["scenario"].as_str().unwrap_or("?");
                            ui.collapsing(format!("{status}: {name}"), |ui| {
                                if let Some(reason) = case["reason"].as_str() { ui.label(reason); }
                                if let Some(psbt) = case["psbt"].as_str() { ui.label(format!("PSBT: {psbt}")); }
                            });
                        }
                    }
                }
                self.checkout_status_ui(ui);
                if let Some(summary) = &self.summary {
                    ui.heading("Verification report");
                    if let Some(tested) = summary.get("baseline_comparison") {
                        ui.collapsing("Baseline comparison recorded for this verification", |ui| {
                            ui.small(tested["baseline_lock"].as_str().unwrap_or(""));
                            if let Some(rows) = tested["checkouts"].as_array() {
                                for row in rows {
                                    ui.label(format!("{}: {} → {} · {}", row["name"].as_str().unwrap_or("?"),
                                        short_revision(&row["baseline_revision"]), short_revision(&row["revision"]), comparison_status(row)));
                                }
                            }
                            ui.small("The full snapshot is saved in this verification's JSON manifest.");
                        });
                    }
                    let counts = &summary["counts"];
                    let failing_labels = ["REGRESSION", "UNCLASSIFIED", "NEW"];
                    let has_issue = failing_labels.iter().any(|label| summary["labels"][*label].as_u64().unwrap_or(0) > 0)
                        || summary["musig2_legs"].as_array().is_some_and(|legs| legs.iter().any(|leg| leg["passed"] != true));
                    if has_issue {
                        ui.colored_label(egui::Color32::RED, "Action needed: review the cases and MuSig2 legs below.");
                    }
                    let expected = &summary["expected"];
                    ui.label(format!("Passed: {}   Failed: {} ({} expected)   Blocked: {} ({} expected)   Completed without full evidence: {}",
                        counts["passed"], counts["failed"], expected["failed"].as_u64().unwrap_or(0),
                        counts["blocked"], expected["blocked"].as_u64().unwrap_or(0), counts["completed"]));
                    let lab_skipped = self.report.as_ref().and_then(|report| report["results"].as_array())
                        .map_or(0, |results| results.iter().filter(|case| case["reason"] == "interop-lab-skipped").count());
                    if lab_skipped > 0 {
                        ui.label(format!("{lab_skipped} completed cases passed Caravan and SPDK; only Interop Lab is missing, which the release check runs."));
                    }
                    if let Some(results) = self.report.as_ref().and_then(|report| report["results"].as_array()) {
                        for (prefix, title) in [("bip375-", "BIP-375"), ("bip376-", "BIP-376"), ("musig2-sp-", "MuSig2 + Silent Payments")] {
                            let cases: Vec<_> = results.iter().filter(|case| case["name"].as_str().is_some_and(|name| name.starts_with(prefix))).collect();
                            if !cases.is_empty() {
                                let passed = cases.iter().filter(|case| case["status"] == "passed").count();
                                let failed = cases.iter().filter(|case| case["status"] == "failed").count();
                                let blocked = cases.iter().filter(|case| case["status"] == "blocked").count();
                                let completed = cases.iter().filter(|case| case["status"] == "completed").count();
                                ui.label(format!("{title}: {passed} passed, {failed} failed, {blocked} blocked, {completed} signing rounds completed"));
                            }
                        }
                    }
                    if let Some(legs) = summary["musig2_legs"].as_array() {
                        for leg in legs {
                            ui.collapsing(format!("MuSig2 {}: {}", leg["architecture"].as_str().unwrap_or("?"),
                                if leg["passed"] == true { "PASS" } else { "ISSUE" }), |ui| {
                                ui.label(leg["line"].as_str().or_else(|| leg["reason"].as_str())
                                    .unwrap_or("No detail was recorded."));
                            });
                        }
                    }
                    if let Some(report) = &self.report {
                        if let Some(results) = report["results"].as_array() {
                            for result in results {
                                let name = result["name"].as_str().unwrap_or("?");
                                let status = result["status"].as_str().unwrap_or("?");
                                let label = result["label"].as_str().unwrap_or("");
                                let needs_attention = failing_labels.contains(&label) || status == "failed";
                                let title = egui::RichText::new(format!("{label}  {status}  {name}"))
                                    .color(if needs_attention { egui::Color32::RED } else { ui.visuals().text_color() });
                                egui::CollapsingHeader::new(title).default_open(needs_attention).show(ui, |ui| {
                                    ui.label(result["reason"].as_str().unwrap_or("No further detail recorded."));
                                });
                            }
                        }
                    }
                    if let Some(path) = summary["report"].as_str() {
                        if ui.link("Open full HTML report").clicked() {
                            if let Err(error) = report_url(Path::new(path))
                                .and_then(|url| webbrowser::open(url.as_str()).map_err(|error| error.to_string())) {
                                self.message = format!("Could not open HTML report: {error}");
                            }
                        }
                        ui.small(path);
                    }
                }
                if self.profile != LIVE_PROFILE {
                ui.separator();
                ui.heading("Update a baseline after code changes");
                ui.label("Pins record exact clean checkout revisions. Review the changes, update the lock, rerun verification, then review expectations.yaml together with the lock. Use an original profile to update pins; fetched profiles reproduce existing pins.");
                ui.horizontal(|ui| {
                    let ready = self.running.is_none() && !missing_live_profile;
                    if ui.add_enabled(ready && self.profile < 2, egui::Button::new("Preview pin changes")).clicked() { self.start(Action::PinPreview); }
                    if ui.add_enabled(ready && self.profile < 2 && self.pin_reviewed && self.pin_changes.as_ref().is_some_and(|v| v["changed"].as_object().is_some_and(|c| !c.is_empty())),
                        egui::Button::new("Update pins")).clicked() { self.start(Action::Pin); }
                });
                if let Some(changes) = &self.pin_changes {
                    if let Some(items) = changes["changed"].as_object() {
                        if items.is_empty() { ui.label("All checkout revisions already match the lock."); }
                        for (name, item) in items {
                            ui.label(format!("{name}: {} → {}", item["from"].as_str().unwrap_or("unpinned"),
                                item["to"].as_str().unwrap_or("?")));
                        }
                    }
                }
                }
            });
        });
    }
}

fn main() -> eframe::Result {
    let root = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(".."));
    let root = root.canonicalize().unwrap_or(root);
    eframe::run_native(
        "BIP-375 interoperability",
        eframe::NativeOptions::default(),
        Box::new(move |_cc| Ok(Box::new(App::new(root)))),
    )
}

#[cfg(test)]
mod tests {
    use super::{
        affected_project, check_mode, cli_arguments, cli_path, comparison_state, comparison_status,
        create_live_profile, live_profile_source, needs_setup, report_url, Action, App,
        COMPARISON_BASELINES, PROJECTS, SETUP_GUIDE,
    };
    use std::ffi::OsStr;
    use std::path::Path;
    use std::process::Command;
    use serde_json::json;

    #[test]
    fn run_and_comparison_default_to_the_bip375_baseline() {
        let app = App::new(std::env::temp_dir());
        assert_eq!(super::PROFILES[app.profile].1, "baseline/interop.yaml");
        assert_eq!(COMPARISON_BASELINES[app.comparison_baseline].1, "baseline/interop.lock");
    }

    #[test]
    fn affected_preview_uses_one_backend_or_all_scenarios() {
        let comparison = json!({"checkouts": [
            {"name": "jade", "status": "ahead", "dirty": false},
            {"name": "coldcard", "status": "matches", "dirty": false}
        ]});
        assert_eq!(PROJECTS[affected_project(&comparison).unwrap()], "jade");
        let dirty = json!({"checkouts": [{"name": "caravan", "status": "matches", "dirty": true}]});
        assert_eq!(PROJECTS[affected_project(&dirty).unwrap()], "caravan");
        for rows in [
            json!([{"name": "jade", "status": "ahead"}, {"name": "coldcard", "status": "behind"}]),
            json!([{"name": "embit", "status": "ahead"}]),
            json!([{"name": "silent-pay", "status": "missing"}]),
            json!([{"name": "bip375-test-generator", "status": "unpinned"}]),
        ] {
            assert_eq!(affected_project(&json!({"checkouts": rows})), Some(0));
        }
        assert_eq!(affected_project(&json!({"checkouts": [{"name": "jade", "status": "matches", "dirty": false}]})), None);
    }

    #[test]
    fn audit_baseline_does_not_switch_the_run_profile_or_gate() {
        for (_, lock) in COMPARISON_BASELINES {
            let compare = cli_arguments("interop.yaml", "jade", false, Action::Compare, Some(lock));
            assert_eq!(&compare[4..], &["compare", "--baseline-lock", lock]);
            for action in [Action::Preview, Action::Check] {
                let args = cli_arguments("interop.yaml", "jade", true, action, Some(lock));
                assert_eq!(&args[..5], &["-m", "bip375_interop.cli", "--config", "interop.yaml", "--allow-dirty"]);
                assert!(args.contains(&"--exhaustive".to_string()));
                assert!(!args.contains(&"--release".to_string()));
                assert_eq!(&args[args.len()-2..], &["--baseline-lock", lock]);
            }
        }
        let baseline = cli_arguments("baseline-musig2/interop.yaml", "harness", false, Action::Check, None);
        assert!(baseline.contains(&"--release".to_string()));
        assert!(!baseline.contains(&"--baseline-lock".to_string()));
    }

    #[test]
    fn refreshed_comparison_preserves_the_tested_snapshot() {
        let mut app = App::new(std::env::temp_dir());
        let tested = json!({"baseline_lock": "baseline/interop.lock", "lock_sha256": "old-lock",
            "checkouts": [{"name": "caravan", "path": "/src/caravan", "revision": "abc",
                "status": "matches", "dirty": true, "diff_sha256": "tested-diff"}]});
        let summary = json!({"baseline_comparison": tested});
        app.finish(Action::Check, 0, &summary.to_string(), "");
        let mut current = tested.clone();
        current["checkouts"][0]["diff_sha256"] = json!("new-diff");
        app.finish(Action::Compare, 0, &current.to_string(), "");
        assert_eq!(app.summary.as_ref().unwrap()["baseline_comparison"], tested);
        assert_ne!(comparison_state(app.comparison.as_ref().unwrap()), comparison_state(&tested));

        let preview = json!({"cases": [], "baseline_comparison": tested});
        app.finish(Action::Preview, 0, &preview.to_string(), "");
        assert_eq!(comparison_state(app.comparison.as_ref().unwrap()), comparison_state(&tested));
        current["lock_sha256"] = json!("new-lock");
        assert_ne!(comparison_state(&current), comparison_state(&tested));
    }

    #[test]
    fn comparison_status_keeps_dirty_and_commit_differences_visible() {
        assert_eq!(comparison_status(&json!({"status": "matches", "dirty": true})), "Uncommitted changes");
        assert_eq!(comparison_status(&json!({"status": "ahead", "ahead": 3, "dirty": true})), "3 ahead + uncommitted");
        assert_eq!(comparison_status(&json!({"status": "diverged", "ahead": 2, "behind": 4})), "Diverged: 2 ahead, 4 behind");
    }

    #[test]
    fn progress_refreshes_the_comparison_before_a_preflight_failure() {
        let mut app = App::new(std::env::temp_dir());
        let comparison = json!({"checkouts": [{"name": "caravan", "status": "matches", "dirty": true}]});
        app.sender.send(super::Event::Progress(json!({"stage": "baseline-comparison", "comparison": comparison}))).unwrap();
        app.sender.send(super::Event::Finished(Action::Check, 2, String::new(), "error: preflight failed".into())).unwrap();
        app.receive();
        assert_eq!(app.comparison, Some(comparison));
        assert!(app.summary.is_none());
        assert!(app.message.contains("preflight failed"));
    }

    #[test]
    fn verification_report_replaces_the_old_preview_and_allows_a_new_one() {
        let preview = json!({"cases": [{"scenario": "case", "status": "selected"}]});
        let summary = json!({"counts": {"passed": 1, "failed": 0, "blocked": 0, "completed": 0}});
        for exit_code in [0, 1] {
            let mut app = App::new(std::env::temp_dir());
            app.finish(Action::Preview, 0, &preview.to_string(), "");
            assert!(app.selection.is_some());

            app.finish(Action::Check, exit_code, &summary.to_string(), "");
            assert!(app.selection.is_none());
            assert_eq!(app.summary, Some(summary.clone()));

            app.finish(Action::Preview, 0, &preview.to_string(), "");
            assert_eq!(app.selection, Some(preview.clone()));
        }

        let mut app = App::new(std::env::temp_dir());
        app.finish(Action::Preview, 0, &preview.to_string(), "");
        app.finish(Action::Check, 2, "", "error: preflight failed");
        assert_eq!(app.selection, Some(preview));
        assert!(app.summary.is_none());
    }

    #[test]
    fn comparison_renders_at_desktop_widths_with_a_reviewed_delta() {
        let mut app = App::new(std::env::temp_dir());
        app.set_comparison(json!({"checkouts": [
            {"name": "bip375-test-generator", "status": "unpinned", "revision": "a".repeat(40)},
            {"name": "seedsigner", "status": "diverged", "ahead": 155, "behind": 9,
                "baseline_revision": "b".repeat(40), "revision": "c".repeat(40),
                "path": "/Users/test/src/seedsigner", "changed_files": ["src/signing.py"],
                "commits": [{"revision": "c".repeat(40), "subject": "Update signing"}]},
            {"name": "caravan", "status": "matches", "dirty": true, "uncommitted_files": ["src/psbt.ts"]}
        ]}));
        app.reviewed_checkout = Some("seedsigner".into());
        for width in [640.0, 900.0] {
            let context = eframe::egui::Context::default();
            let input = eframe::egui::RawInput {
                screen_rect: Some(eframe::egui::Rect::from_min_size(eframe::egui::Pos2::ZERO, eframe::egui::vec2(width, 800.0))),
                ..Default::default()
            };
            let mut right_edge = 0.0;
            let mut output = context.run_ui(input, |root_ui| {
                eframe::egui::CentralPanel::default().show(root_ui, |ui| {
                    app.comparison_ui(ui, false);
                    right_edge = ui.min_rect().right();
                });
            });
            output.textures_delta.clear();
            assert!(!output.shapes.is_empty());
            assert!(right_edge <= width, "Comparison overflows at width {width}: {right_edge}");
        }
    }

    #[test]
    fn live_checkout_status_stays_hidden_when_the_comparison_is_cleared() {
        fn has_checkout_heading(shape: &eframe::egui::epaint::Shape) -> bool {
            match shape {
                eframe::egui::epaint::Shape::Text(text) => text.galley.text() == "Checkout status",
                eframe::egui::epaint::Shape::Vec(shapes) => shapes.iter().any(has_checkout_heading),
                _ => false,
            }
        }
        let mut app = App::new(std::env::temp_dir());
        app.doctor = Some(json!([{"name": "caravan", "revision": "abc", "dirty": true}]));
        for (profile, comparison, expected_visible) in [
            (super::LIVE_PROFILE, Some(json!({"checkouts": []})), false),
            (super::LIVE_PROFILE, None, false),
            (0, None, true),
        ] {
            app.profile = profile;
            app.comparison = comparison;
            let context = eframe::egui::Context::default();
            let input = eframe::egui::RawInput {
                screen_rect: Some(eframe::egui::Rect::from_min_size(eframe::egui::Pos2::ZERO, eframe::egui::vec2(800.0, 600.0))),
                ..Default::default()
            };
            let mut output = context.run_ui(input, |root_ui| {
                eframe::egui::CentralPanel::default().show(root_ui, |ui| {
                    app.checkout_status_ui(ui);
                });
            });
            output.textures_delta.clear();
            assert_eq!(output.shapes.iter().any(|shape| has_checkout_heading(&shape.shape)), expected_visible);
        }
    }

    fn git(root: &Path, args: &[&str]) {
        assert!(Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .status()
            .unwrap()
            .success());
    }

    #[test]
    fn live_profile_uses_main_checkout_and_preserves_an_existing_copy() {
        let temp = std::env::temp_dir().join(format!(
            "bip375-gui-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let main = temp.join("main");
        let worktree = temp.join("worktree");
        std::fs::create_dir_all(&main).unwrap();
        git(&main, &["init", "-q"]);
        git(
            &main,
            &[
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "initial",
            ],
        );
        git(
            &main,
            &[
                "worktree",
                "add",
                "-q",
                "--detach",
                worktree.to_str().unwrap(),
            ],
        );
        let source = main.join("interop.yaml");
        std::fs::write(&source, "checkouts: {}\n").unwrap();

        assert_eq!(
            live_profile_source(&worktree),
            source.canonicalize().unwrap()
        );
        create_live_profile(&worktree, &source).unwrap();
        assert_eq!(
            std::fs::read_to_string(worktree.join("interop.yaml")).unwrap(),
            "checkouts: {}\n"
        );
        assert!(create_live_profile(&worktree, &source).is_err());

        std::fs::remove_file(&source).unwrap();
        let example = worktree.join("config/interop.example.yaml");
        std::fs::create_dir_all(example.parent().unwrap()).unwrap();
        std::fs::write(&example, "checkouts: {}\n").unwrap();
        assert_eq!(live_profile_source(&worktree), example);

        git(
            &main,
            &["worktree", "remove", "--force", worktree.to_str().unwrap()],
        );
        std::fs::remove_dir_all(&temp).unwrap();
    }

    #[test]
    fn setup_link_follows_checkout_errors_and_the_guide_exists() {
        assert!(needs_setup("Could not complete: preflight failed:\n  - caravan: x is not built"));
        assert!(needs_setup("Could not complete: spdk: missing checkout /tmp/spdk"));
        assert!(needs_setup("Could not complete: spdk: expected 7df4615, found f93de4d"));
        assert!(!needs_setup("Check finished. Review coverage and findings below."));
        let guide = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(SETUP_GUIDE);
        assert!(std::fs::read_to_string(guide).unwrap().contains("### Setting up a baseline"));
    }

    #[test]
    fn report_link_encodes_spaces_in_the_local_path() {
        let path = std::env::temp_dir().join(format!("bip375 report {}.html", std::process::id()));
        std::fs::write(&path, "<!doctype html>").unwrap();
        let url = report_url(&path).unwrap();
        assert_eq!(url.scheme(), "file");
        assert!(url.as_str().contains("%20report%20"));
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn only_musig2_harness_uses_release_gate() {
        assert_eq!(check_mode("interop.yaml", "harness"), "--exhaustive");
        assert_eq!(
            check_mode("baseline/interop.yaml", "harness"),
            "--exhaustive"
        );
        assert_eq!(
            check_mode("baseline-musig2/interop.yaml", "harness"),
            "--release"
        );
        assert_eq!(
            check_mode("baseline-musig2/interop.fetched.yaml", "harness"),
            "--release"
        );
        assert_eq!(
            check_mode("baseline-musig2/interop.yaml", "jade"),
            "--exhaustive"
        );
    }

    #[test]
    fn cli_path_finds_user_tools_from_a_desktop_app() {
        let root = Path::new("/tmp/interop");
        let path = cli_path(
            root,
            OsStr::new("/usr/bin:/bin"),
            Some(OsStr::new("/Users/test")),
        )
        .unwrap();
        let paths: Vec<_> = std::env::split_paths(&path).collect();
        assert!(paths.contains(&Path::new("/opt/homebrew/bin").to_path_buf()));
        assert!(paths.contains(&Path::new("/usr/local/bin").to_path_buf()));
        assert!(paths.contains(&Path::new("/Users/test/.local/bin").to_path_buf()));
        assert!(paths.contains(&Path::new("/Users/test/.cargo/bin").to_path_buf()));
    }
}
