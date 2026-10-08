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
    Doctor,
    Fetch,
    Preview,
    Check,
    PinPreview,
    Pin,
}

enum Event {
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
}

impl App {
    fn new(root: PathBuf) -> Self {
        let (sender, receiver) = mpsc::channel();
        Self {
            root,
            profile: 1,
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
        let sender = self.sender.clone();
        std::thread::spawn(move || {
            let result = run_cli(
                &root,
                &config,
                &project,
                allow_dirty,
                action,
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
                Event::Progress(value) => {
                    let stage = value["stage"].as_str().unwrap_or("");
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
                        self.total += 2;
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
            return;
        }
        match action {
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
        .args(["-m", "bip375_interop.cli", "--config", config]);
    if allow_dirty && action != Action::PinPreview && action != Action::Pin {
        command.arg("--allow-dirty");
    }
    match action {
        Action::Doctor => {
            command.arg("doctor");
        }
        Action::Fetch => {
            command.args(["fetch", "--in-place"]);
        }
        Action::Preview => {
            command.args(["check", "--project", project, "--dry-run"]);
            command.arg(check_mode(config, project));
        }
        Action::Check => {
            command.args(["check", "--project", project, "--progress-json"]);
            command.arg(check_mode(config, project));
        }
        Action::PinPreview => {
            command.args(["pin", "--dry-run"]);
        }
        Action::Pin => {
            command.arg("pin");
        }
    }
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
            let missing_live_profile = self.profile == 2 && !self.root.join("interop.yaml").is_file();
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
                if let Some(summary) = &self.summary {
                    ui.heading("Verification report");
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
                ui.separator();
                ui.heading("Update a baseline after code changes");
                ui.label("Pins record exact clean checkout revisions. Review the changes, update the lock, rerun verification, then review expectations.yaml together with the lock. Use an original profile to update pins; fetched profiles reproduce existing pins.");
                ui.horizontal(|ui| {
                    let ready = self.running.is_none() && !missing_live_profile;
                    if ui.add_enabled(ready && self.profile < 3, egui::Button::new("Preview pin changes")).clicked() { self.start(Action::PinPreview); }
                    if ui.add_enabled(ready && self.profile < 3 && self.pin_reviewed && self.pin_changes.as_ref().is_some_and(|v| v["changed"].as_object().is_some_and(|c| !c.is_empty())),
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
        check_mode, cli_path, create_live_profile, live_profile_source, needs_setup, report_url,
        SETUP_GUIDE,
    };
    use std::ffi::OsStr;
    use std::path::Path;
    use std::process::Command;

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
