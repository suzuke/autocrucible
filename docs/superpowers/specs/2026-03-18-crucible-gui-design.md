# Crucible GUI — Design Spec

## Overview

A standalone Tauri v2 desktop application for post-run analysis of crucible experiments. The primary goal is making agent reasoning visible — understanding *what* the agent did and *why* at each iteration.

**Phase 1 (MVP):** Post-run experiment analysis
**Future:** Project creation/configuration, experiment control, live monitoring

## Architecture

### Approach: Direct File Reading (方案 A)

The Tauri app reads experiment data directly from the filesystem. No API server, no coupling with crucible core.

- **Rust backend** (Tauri commands): reads `results-*.jsonl`, `logs/iter-N/`, git history via `git2`
- **React frontend**: renders timeline, agent reasoning, diffs, charts
- **Future operations**: spawn `crucible` CLI as subprocess

### Why This Approach

- All data already exists on disk (JSONL + git + log files)
- Zero coupling — independent development and release
- Future operations via CLI subprocess (simple, effective)
- Can evolve to API server (方案 B) later if needed

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Desktop framework | Tauri v2 | Rust backend + native webview |
| Frontend | React + TypeScript | UI components |
| Styling | Tailwind CSS | Utility-first CSS |
| Charts | Recharts | Metric trend visualization |
| Diff viewer | react-diff-viewer | Syntax-highlighted code diffs |
| Git access | git2 crate | Read commits, diffs, tags |
| Serialization | serde + serde_json | JSONL/config parsing |

## Data Model & Tauri Commands

### Project Management

```
open_project(path: String) → ProjectInfo
  { name, config, tags: Vec<String> }

list_recent_projects() → Vec<ProjectInfo>
  // Persisted in Tauri app data
```

### Experiment Records

```
list_experiments(path: String) → Vec<ExperimentTag>
  { tag, record_count, best_metric, date_range }

get_results(path: String, tag: String) → Vec<ExperimentRecord>
  // Parses results-{tag}.jsonl
```

`ExperimentRecord` fields (matching crucible's JSONL schema):
- commit, metric_value, status, description, iteration, timestamp
- delta, delta_percent, files_changed, diff_stats
- duration_seconds, usage (tokens + cost), log_dir, beam_id

### Iteration Details

```
get_iteration_log(path: String, tag: String, iter: u32)
  → { agent_reasoning: String, run_log: String }
  // Reads logs/iter-{N}/agent.txt and run.log
```

### Git History

```
get_commits(path: String, branch: String) → Vec<Commit>
  { hash, message, timestamp, files_changed }

get_diff(path: String, commit_a: String, commit_b: String) → Vec<FileDiff>
  // Unified diff per file

get_failed_attempts(path: String, tag: String) → Vec<FailedAttempt>
  { seq, commit, diff }
  // Reads failed/{tag}/{seq} tags
```

### Data Flow

```
Frontend invoke("get_results", {path, tag})
  → Tauri IPC
  → Rust reads results-{tag}.jsonl, serde deserialize per line
  → Returns Vec<ExperimentRecord> as JSON
  → React state update → render
```

### Design Decisions

- **Lazy loading**: all data fetched on demand, no background scanning
- **Readonly git**: `git2` opens repo in readonly mode
- **Fault-tolerant JSONL**: skip malformed lines (matches crucible's results.py behavior)

## UI Layout & Pages

### Overall Layout

Left sidebar navigation + right content area. Dark theme default.

### Page 1: Home / Project Selector

- Recent projects list (persisted to Tauri app data)
- "Open Project" button → system directory picker
- Project card: name, best metric, last run date

### Page 2: Experiment Overview (main page after opening project)

**Top section: Metric Trend Chart**
- X axis: iteration number
- Y axis: metric value
- Color coding: green dots = keep, gray = discard, red = crash
- Line connecting kept values to show improvement trajectory

**Bottom section: Iteration Timeline Table**
- Columns: `#` | Status icon | Metric | Delta | Cost | Duration | Description
- Click row → navigate to iteration detail
- Sortable/filterable

**Sidebar: Experiment tag switcher** (same project may have multiple runs)

### Page 3: Iteration Detail

**Summary card** (top):
- Metric value, delta%, duration, files changed, cost

**Three tabs:**
1. **Agent Reasoning** — full `agent.txt` content, rendered as markdown
2. **Code Diff** — syntax-highlighted unified diff, collapsible per file
3. **Run Log** — raw `run.log` output, terminal-style monospace rendering

### Page 4: Git History (future)

- Commit list with clickable diffs
- Failed attempt tags shown in red

### Page 5: Project Settings (future)

- Visual editor for `config.yaml`
- New project wizard

## Phase 1 Scope

### Included

1. Project selector (open directory + recent projects)
2. Experiment overview (metric chart + iteration timeline)
3. Iteration detail (agent reasoning / code diff / run log tabs)

### Excluded

- Start/stop experiments
- Create/edit project settings
- Git history as standalone page (diffs shown in iteration detail)
- Live monitoring (streaming)
- Multi-project comparison

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Directory is not a crucible project | Toast: "找不到 .crucible/config.yaml" |
| JSONL has malformed lines | Skip lines, toast: "N 行無法解析" |
| `logs/iter-N/` missing | Tab shows "此迭代無日誌" |
| Git repo corrupted | Diff tab degrades: "無法讀取 git 歷史" |

## Project Structure

```
crucible-gui/
├── src-tauri/              # Rust backend
│   ├── src/
│   │   ├── main.rs
│   │   ├── commands/       # Tauri commands
│   │   │   ├── project.rs  # open_project, list_recent_projects
│   │   │   ├── results.rs  # list_experiments, get_results
│   │   │   ├── logs.rs     # get_iteration_log
│   │   │   └── git.rs      # get_commits, get_diff, get_failed_attempts
│   │   └── parsers/        # Data parsing
│   │       ├── jsonl.rs     # JSONL parser
│   │       └── config.rs    # config.yaml parser
│   └── Cargo.toml          # tauri, git2, serde, serde_json
├── src/                    # React frontend
│   ├── components/         # Reusable UI components
│   │   ├── MetricChart.tsx
│   │   ├── IterationTable.tsx
│   │   ├── DiffViewer.tsx
│   │   ├── AgentReasoning.tsx
│   │   └── RunLog.tsx
│   ├── pages/
│   │   ├── Home.tsx        # Project selector
│   │   ├── Overview.tsx    # Experiment overview
│   │   └── Detail.tsx      # Iteration detail
│   ├── hooks/
│   │   └── useTauri.ts     # Tauri command invocation wrapper
│   ├── App.tsx
│   └── main.tsx
├── package.json            # react, recharts, react-diff-viewer, tailwindcss
└── tauri.conf.json
```
