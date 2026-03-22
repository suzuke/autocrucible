# Research Pipeline Mode — Design Spec

> 讓 LLM 像人類研究者一樣做研究——先提假說、再設計實驗、再執行分析——每一步都有品質門檻，且完成後鎖定不可竄改，防止事後改假說來迎合結果。

## 背景

Crucible 目前只支援單步優化：一個 metric、一個迴圈、一組可編輯檔案。這對已知目標的優化很有效，但無法處理開放式研究——需要多個階段、每步有不同的評估標準、且前一步的產出應鎖定不可回頭修改（pre-registration 模式）。

Stanford Andrew Hall 論文驗證了這個問題：LLM 在有明確目標時表現好，但開放式探索時品質差。Pipeline 的目的是把開放式研究拆成多個有護欄的步驟。

## 設計決策

| 決策 | 選擇 | 理由 |
|------|------|------|
| 使用場景 | 學術研究優先 | 差異化價值；工程場景是子集，放寬 lock 即可 |
| Gate 失敗 | 強制停止 + `--force-continue` | Pre-registration 精神，護欄要有意義 |
| Context 傳遞 | 產出物 + 最終 metric | 不帶迭代歷史，省 token、防誤導 |
| Git 策略 | 單一 branch + 步驟完成打 tag | 簡單、歷史連貫、tag 鎖定 |
| 架構 | PipelineOrchestrator wrapper | 不動現有 Orchestrator，每步是標準 run |

## 架構

### 流程

```
crucible run --tag study1
        │
        ▼
   mode == "research"?
   ├── No  → 現有 Orchestrator.run_loop()（零改動）
   └── Yes → PipelineOrchestrator.run_pipeline()
              │
              for step in pipeline.steps:
              │
              │  1. merge(global_config, step_config)
              │     - 累加 readonly（前步 editable）
              │     - 設定該步 metric/commands/agent
              │
              │  2. Orchestrator(merged_config)
              │     .init() or .resume()
              │     .run_loop(max_iterations)
              │
              │  3. 檢查 gate
              │     best < gate? → 停止（或 --force-continue）
              │
              │  4. git tag step/{tag}/{step_name}
              │     鎖定該步產出
              │
              │  5. 下一步 context 注入：
              │     "Previous: hypothesize — 0.82 ✓"
```

### 核心類：PipelineOrchestrator

**關鍵整合細節：**

1. **`load_config()` 驗證**：research mode 跳過 top-level `files.editable`、`commands.*`、`metric.*` 的 `_require()` 檢查，改為驗證每步的這些欄位。Top-level 欄位用 placeholder 預設值填充（空 list、空 string），讓 `Config` dataclass 保持 valid。
2. **Branch 建立**：`PipelineOrchestrator` 自行呼叫 `git.create_branch(tag)` 一次。每步建立 `Orchestrator` 時跳過 `init()` 的 branch creation，改用 `resume()` 或新增的 `init_step()` 方法（只初始化 results log + setup，不建 branch）。
3. **Results 檔案**：`results_filename()` 新增 `step_name` 參數：`results_filename(tag, step_name=None)` → `results-{tag}-{step_name}.jsonl`。
4. **Agent 建立**：複製 cli.py 的 `create_agent()` 呼叫模式，傳入 `config.agent` + `hidden_files` + `editable_files` kwargs。

```python
class PipelineOrchestrator:
    """串聯多個 Orchestrator 實例，每步是一個獨立的優化迴圈。"""

    def __init__(self, config: Config, workspace: Path, tag: str,
                 force_continue: bool = False):
        self.config = config
        self.workspace = workspace
        self.tag = tag
        self.force_continue = force_continue
        self.step_results: dict[str, StepResult] = {}
        self.git = GitManager(
            workspace=workspace,
            branch_prefix=config.git.branch_prefix,
            tag_failed=config.git.tag_failed,
        )

    def run_pipeline(self, from_step: str | None = None,
                     only_step: str | None = None) -> PipelineResult:
        """執行 pipeline 的全部或部分步驟。"""
        steps = self._resolve_steps(from_step, only_step)

        # 建 branch（只做一次）
        if not self.git.branch_exists(self.tag):
            self.git.create_branch(self.tag)
        else:
            self.git.checkout_branch(self.tag)

        for i, step_cfg in enumerate(steps):
            # 1. 計算有效 config（累加前步 readonly）
            merged = self._merge_step_config(step_cfg, i)

            # 2. 建立 agent（複製 cli.py 的模式）
            agent = ClaudeCodeAgent(
                timeout=merged.constraints.timeout_seconds,
                model=merged.agent.model,
                system_prompt_file=merged.agent.system_prompt,
                hidden_files=merged.files.hidden,
                editable_files=merged.files.editable,
                language=merged.agent.language,
            )

            # 3. 建立 orchestrator（傳入 step-specific results path）
            orch = Orchestrator(merged, self.workspace, self.tag, agent)
            # 覆蓋 results path 為 step-specific
            orch.results = ResultsLog(
                self.workspace / results_filename(self.tag, step_cfg.step)
            )

            # 4. init_step()（不建 branch，只初始化 results + context）
            if self._step_has_progress(step_cfg.step):
                orch.resume_step()  # 讀取既有 results，續跑
            else:
                orch.init_step()    # 初始化 results log，跑 setup

            # 5. 跑迴圈
            orch.run_loop(max_iterations=step_cfg.max_iterations)

            # 6. 檢查 gate
            best = orch.results.best(merged.metric.direction)
            if step_cfg.gate is not None:
                if best is None:
                    # 所有迭代都 crash，沒有有效結果 → gate 失敗
                    if not self.force_continue:
                        return PipelineResult(stopped_at=step_cfg.step,
                                              reason="no_successful_iterations")
                else:
                    passed = self._check_gate(best.metric_value,
                                              step_cfg.gate,
                                              merged.metric.direction)
                    if not passed and not self.force_continue:
                        return PipelineResult(stopped_at=step_cfg.step,
                                              reason="gate_failed")

            # 7. 打 tag 鎖定（force 覆蓋，支援 rerun）
            self.git.tag_step(self.tag, step_cfg.step, force=True)

            # 8. 記錄該步結果
            self.step_results[step_cfg.step] = StepResult(
                metric=best.metric_value if best else None,
                iterations=orch._iteration,
                status="passed" if best else "no_results",
            )

        return PipelineResult(completed=True, step_results=self.step_results)

    def _merge_step_config(self, step: PipelineStepConfig,
                           step_index: int) -> Config:
        """合併全域 config + 步驟 config，累加前步 editable 為 readonly。"""
        merged = copy.deepcopy(self.config)

        # 覆蓋步驟專屬設定
        merged.commands = step.commands
        merged.metric = step.metric
        merged.files = copy.deepcopy(step.files)
        merged.agent.instructions = step.instructions

        if step.max_iterations:
            merged.constraints.max_iterations = step.max_iterations
        if step.agent:
            for field in ["model", "system_prompt", "language", "base_url"]:
                val = getattr(step.agent, field, None)
                if val is not None:
                    setattr(merged.agent, field, val)

        # 累加前步 editable 為 readonly（pre-registration 鎖定）
        if self.config.pipeline.lock_outputs:
            prev_steps = self.config.pipeline.steps[:step_index]
            for prev in prev_steps:
                for f in prev.files.editable:
                    if f not in merged.files.readonly:
                        merged.files.readonly.append(f)

        return merged

    def _step_has_progress(self, step_name: str) -> bool:
        """檢查該步是否有既有 results 檔案（resume 用）。"""
        results_path = self.workspace / results_filename(self.tag, step_name)
        return results_path.exists() and results_path.stat().st_size > 0

    def _resolve_steps(self, from_step, only_step):
        """解析要跑哪些步驟。驗證 prerequisites。"""
        steps = self.config.pipeline.steps
        if only_step:
            # --step: 只跑一步，但仍累加前步 readonly
            match = [s for s in steps if s.step == only_step]
            if not match:
                raise ConfigError(f"Unknown step: {only_step}")
            # 驗證前置步驟已完成（有 tag）
            idx = next(i for i, s in enumerate(steps) if s.step == only_step)
            for prev in steps[:idx]:
                if not self.git.tag_exists(f"step/{self.tag}/{prev.step}"):
                    raise ConfigError(
                        f"Step '{only_step}' requires '{prev.step}' "
                        f"to be completed first (no tag found)"
                    )
            return match
        if from_step:
            idx = next((i for i, s in enumerate(steps) if s.step == from_step), None)
            if idx is None:
                raise ConfigError(f"Unknown step: {from_step}")
            return steps[idx:]
        return steps
```

### Orchestrator 小幅修改

為了支援 pipeline，`Orchestrator` 需新增兩個薄方法（不改現有邏輯）：

```python
# orchestrator.py 新增

def init_step(self):
    """Pipeline 用：初始化 results log + 跑 setup command，但不建 branch。"""
    self.results.init()
    self._run_setup_command()
    # 不呼叫 git.create_branch()

def resume_step(self):
    """Pipeline 用：從既有 results 續跑，不切 branch。"""
    records = self.results.read_all()
    if records:
        self._iteration = records[-1].iteration
```

這讓 "不動 orchestrator.py" 的承諾改為 "只加兩個不影響現有行為的薄方法"。

### 新增 Dataclasses

```python
# config.py

@dataclass
class PipelineStepConfig:
    step: str                          # 步驟名稱
    instructions: str                  # 該步的 program.md 路徑
    files: FilesConfig                 # editable/readonly/hidden
    commands: CommandsConfig           # run/eval commands
    metric: MetricConfig               # metric name + direction
    gate: float | None = None          # 通過門檻
    max_iterations: int | None = None  # 覆蓋全域值
    agent: AgentConfig | None = None   # 覆蓋全域 agent 設定

@dataclass
class PipelineConfig:
    steps: list[PipelineStepConfig]
    lock_outputs: bool = True          # 前步 editable → 下步 readonly

@dataclass
class StepResult:
    metric: float | None
    iterations: int
    status: str  # "passed" | "gate_failed" | "max_iterations"

@dataclass
class PipelineResult:
    completed: bool = False
    stopped_at: str | None = None
    reason: str | None = None
    step_results: dict[str, StepResult] = field(default_factory=dict)
```

### Config 根層變更

```python
@dataclass
class Config:
    # 既有欄位不動...
    mode: str = "optimize"             # "optimize" | "research"
    pipeline: PipelineConfig | None = None
```

### Config 驗證

**optimize mode**（預設）：現有 `_require()` 邏輯不變，完全不看 pipeline 欄位。

**research mode**：
- 跳過 top-level `files.editable`、`commands.run`、`commands.eval`、`metric.name`、`metric.direction` 的 require 檢查
- 改為驗證 `pipeline` 欄位存在，且每步都有這些必填欄位
- Top-level `files`/`commands`/`metric` 用 placeholder 預設值填充，讓 `Config` dataclass valid
- 每步 `step` 名稱不可重複
- 每步至少一個 editable 檔案
- `search.strategy: beam` + `mode: research` → 拒絕（v1 不支援）

## Immutability 機制

用現有 `PreToolUse` hooks（跟 hidden files 同機制）：

- 前步 editable 檔案在當前步成為 readonly
- Agent 嘗試 Edit/Write → hook 返回 `"Access denied: locked by step '{step_name}' (pre-registration)"`
- 不需要移動檔案、不需要 branch merge
- 防禦層：guardrails.py 的 editable policy 也會擋（defense-in-depth）

## Git 策略

- 整個 pipeline 用單一 branch：`crucible/{tag}`
- 每步完成打 tag：`step/{tag}/{step_name}`
- 可用 `git show step/{tag}/hypothesize:hypothesis.md` 查看鎖定時的檔案內容
- 失敗 commit 仍用現有 `failed/{tag}/{seq}` 機制
- `git_manager.py` 新增 `tag_step(tag, step_name, force=True)` 和 `tag_exists(tag_name)` 方法
- Re-run 時 `tag_step` 用 `-f` 覆蓋既有 tag

## Context 傳遞

每步的 agent prompt 包含：

1. **該步的 program.md**（instructions）
2. **前步摘要**（在 `_section_state()` 裡注入）：
   ```
   --- Pipeline Progress ---
   ✓ hypothesize — hypothesis_score: 0.82 (5 iterations)
   ▶ design (current step, 2/3)
   ○ execute — pending
   ```
3. **前步產出物**可透過 Read tool 讀取（已是 readonly）
4. **不帶**前步迭代歷史

`context.py` 修改：加 `previous_steps: list[StepResult] | None` 參數到 constructor，在 `_section_state()` 裡渲染。

## Results 與 Profiling

- `results_filename(tag, step_name=None)` → 有 step_name 時返回 `results-{tag}-{step_name}.jsonl`，否則 `results-{tag}.jsonl`（向後相容）
- `ExperimentRecord` 加 `step_name: str | None = None`
- `history` 命令支援 `--step` 篩選
- Token profiling 按步驟分開
- 既有 `.gitignore` pattern `results-*.jsonl` 自動涵蓋新格式

## CLI 介面

### `run` 命令擴展

```bash
crucible run --tag study1                     # pipeline 或單步（自動偵測）
crucible run --tag study1 --from-step design  # 從指定步驟開始
crucible run --tag study1 --step hypothesize  # 只跑某一步
crucible run --tag study1 --force-continue    # gate 失敗仍繼續
```

偵測邏輯：載入 config 後檢查 `mode`，research → `PipelineOrchestrator`，optimize → 現有 `Orchestrator`。

### `status` 命令擴展

```bash
crucible status --tag study1
# Pipeline: my-research (3 steps)
#   ✓ hypothesize  — score: 0.82 (5 iters, best @ iter 3)
#   ▶ design       — score: 0.65 (3 iters, running...)
#   ○ execute      — pending
```

偵測邏輯：檢查是否有 `results-{tag}-*.jsonl` 檔案，有則按步驟顯示。

## Config 範例

```yaml
name: "my-research"
mode: research

pipeline:
  lock_outputs: true
  steps:
    - step: hypothesize
      instructions: "hypothesis-program.md"
      files:
        editable: ["hypothesis.md"]
      commands:
        run: "python3 -u check_hypothesis.py 2>&1 | tee run.log"
        eval: "cat run.log"
      metric:
        name: "hypothesis_score"
        direction: "maximize"
      gate: 0.7
      max_iterations: 10

    - step: design
      instructions: "design-program.md"
      files:
        editable: ["analysis_plan.md"]
      commands:
        run: "python3 -u check_design.py 2>&1 | tee run.log"
        eval: "cat run.log"
      metric:
        name: "design_validity"
        direction: "maximize"
      gate: 0.7
      max_iterations: 10

    - step: execute
      instructions: "execute-program.md"
      files:
        editable: ["analysis.py", "results.md"]
        hidden: ["data/raw/"]
      commands:
        run: "python3 -u evaluate_results.py 2>&1 | tee run.log"
        eval: "cat run.log"
      metric:
        name: "result_quality"
        direction: "maximize"
      gate: 0.6
      max_iterations: 15

constraints:
  timeout_seconds: 120
search:
  strategy: "greedy"
```

## 模組變更清單

| 檔案 | 變更類型 | 內容 | 估計行數 |
|------|---------|------|---------|
| `pipeline.py` | **新增** | PipelineOrchestrator, StepResult, PipelineResult | ~150 |
| `config.py` | 修改 | PipelineStepConfig, PipelineConfig dataclasses + 驗證 | ~50 |
| `context.py` | 修改 | `_section_state()` 加 pipeline progress 摘要 | ~15 |
| `cli.py` | 修改 | mode 偵測, `--from-step`/`--step`/`--force-continue`, status 顯示 | ~60 |
| `git_manager.py` | 修改 | `tag_step()` 方法 | ~5 |
| `results.py` | 修改 | `ExperimentRecord` 加 `step_name` 欄位 | ~5 |
| `test_pipeline.py` | **新增** | pipeline 專屬測試 | ~150 |

| `orchestrator.py` | 修改 | 新增 `init_step()` + `resume_step()` 薄方法 | ~15 |

**不動的模組**：agents/, guardrails.py, runner.py

**總計**：~450 行新增/修改

## 向後相容

- `mode` 預設 `"optimize"` → 現有行為，零改動
- 沒有 `pipeline` 欄位 → 單步模式
- 所有現有 config.yaml 不需修改
- 所有現有 examples 不受影響
- `ExperimentRecord` 新欄位 `step_name` 預設 `None`，舊 JSONL 反序列化正常

## 不做的事

- **Phase 4 跨模型**：不在 v1 實作，但 `agent` 覆蓋欄位已預留接口
- **Beam search + pipeline**：複雜度高，v1 只支援 greedy/restart
- **回退機制 (c)**：gate 失敗不回上一步，只停止
- **步驟間共享迭代歷史**：只傳產出物 + metric
