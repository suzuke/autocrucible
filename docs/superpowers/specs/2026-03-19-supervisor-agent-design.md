# Supervisor Agent 設計

## 問題

Crucible 的內層 agent 自主優化 metric，但會發現 gaming 策略（copy-paste 降耦合、interface 重複、常數 inline）讓分數提升但程式碼品質沒有真正改善。目前偵測和應對 gaming 需要人工介入：看 diff、判斷品質、cherry-pick 好的 commit、修補評估腳本、重新啟動。

這個設計用 supervisor agent 自動化這整個流程 — 一個獨立的 Claude LLM session，定期檢視內層 agent 的工作並在需要時介入。

## 設計

### 架構

雙 agent 系統，權限互補：

```
用戶啟動 crucible run（supervisor 開啟）
  │
  SupervisorLoop（新 class，掌控迭代節奏）
  │
  ├─ 呼叫 orchestrator.run_one_iteration() × N 次
  │   （內層 agent 改 source，evaluate 計分）
  │
  ├─ 觸發條件達成 → supervisor review
  │   ├─ 讀最近的 results + diffs
  │   ├─ 判斷：真正改善還是 gaming？
  │   ├─ 輸出：繼續 / 介入
  │   │
  │   └─ 如果介入：
  │       ├─ Git 操作（reset、cherry-pick、revert、branch）
  │       ├─ 修補 evaluate（僅追加）+ program.md
  │       ├─ 語法檢查修補後的 evaluate
  │       ├─ 驗證 frozen baseline 分數
  │       └─ 繼續內層迴圈
  │
  └─ 重複直到 max_rounds 或內層迴圈自然結束
```

#### 模組結構

新檔案：`supervisor.py`，包含 `SupervisorLoop` class。

當 `supervisor.enabled=true` 時，`cli.py` 的 `run` 指令建立 `SupervisorLoop` 而非呼叫 `orchestrator.run_loop()`。`SupervisorLoop`：
- 持有 `Orchestrator` 實例的參考
- 在自己的迴圈裡呼叫 `orchestrator.run_one_iteration()`
- 管理觸發偵測、supervisor LLM 呼叫、介入執行
- 追蹤 round 編號和 supervisor 狀態

`Orchestrator` class 不改動 — `run_one_iteration()` 維持純粹的單次迭代方法。`SupervisorLoop` 只取代外層迴圈（`_run_loop_serial`），不動任何迭代內部邏輯。

```python
# cli.py（簡化）
if config.supervisor.enabled:
    loop = SupervisorLoop(orchestrator, config.supervisor)
    loop.run()
else:
    orchestrator.run_loop()
```

### 權限模型

| 檔案 | 內層 agent | Supervisor LLM |
|------|-----------|----------------|
| Source files (src/**) | 讀 + 寫 | 僅讀 |
| Test files (*.test.*) | 僅讀 | 僅讀 |
| evaluate 腳本 | hidden（看不到）| 讀 + 寫（僅追加）|
| evaluate_baseline（凍結版）| hidden | hidden（Python 程式碼用 subprocess 跑，LLM 看不到）|
| supervisor_objective.md | hidden | hidden（Python 程式碼注入 prompt，LLM 看不到檔案）|
| .crucible/program.md | 僅讀 | 讀 + 寫 |
| results-*.jsonl | N/A（orchestrator 寫入）| 僅讀 |
| .crucible/config.yaml | 僅讀 | 僅讀 |
| git history / diffs | N/A | 讀（透過 tool calls）|
| logs/supervisor/ | N/A | 寫（gitignored）|

透過 `_make_file_hooks()` 搭配不同參數集來對每個 agent 強制執行。

注意：`evaluate_baseline` 對兩個 agent 都是 hidden。Supervisor 的 Python 程式碼（不是 LLM）透過 `ExperimentRunner.execute()` 以 subprocess 方式執行它。Supervisor LLM 永遠不直接讀 baseline 檔案 — 只看到結果分數。

### Supervisor 核心目標（不可修改）

檔案：`.crucible/supervisor_objective.md`，用戶在實驗開始前撰寫。

定義 supervisor 判斷「好的改動」的最終標準，例如：
- 什麼算真正的架構改善
- 什麼算 gaming
- 品質的底線在哪裡

這個檔案對兩個 agent 都是 hidden（系統層級保護，不經過 SDK hooks）。Supervisor 的 Python 程式碼在每次 review 時將內容注入 system prompt。Supervisor LLM 看到它的內容但無法讀取、修改、甚至得知檔案路徑。

如果沒有提供這個檔案，supervisor 使用內建預設目標（「確保改動是真正的品質改善而非 metric gaming」）。

這是整個雙 agent 系統中唯一由人類定義、兩個 agent 都不能碰的東西。它是 supervisor 的 ground truth，就像 frozen baseline 是 metric 的 ground truth。

### 凍結 baseline

首次啟用 supervisor 時，原始 evaluate 腳本被複製為 `evaluate_baseline`（兩個 agent 都 hidden）。每個 supervisor round 結束後，Python 程式碼同時執行：
- Supervisor（可能已修改的）evaluate — 自適應 metric
- 凍結的 baseline evaluate — ground truth

兩個分數都記錄。Drift 偵測是雙向的：
- 如果 baseline 分數改善超過預期 → evaluate 可能變寬鬆了（supervisor 放鬆了限制）
- 如果 baseline 分數退步 → 真正的程式碼退化，自適應 evaluate 沒抓到

如果用戶先不開 supervisor 跑了幾輪，之後才啟用，啟用當下的 evaluate 就成為凍結 baseline。

### 觸發條件

預設：當內層迴圈停滯時觸發。使用 `_count_plateau_streak()` — 從 results 尾部計算連續非 keep 的紀錄數。

可設定為固定間隔（每 N 次迭代 review 一次）。

理由：內層 agent 在進步的時候，supervisor 沒有價值。在停滯時才觸發能省 LLM 成本也省時間。

### Supervisor review 輸入

每次 review，supervisor 收到（context 有上限）：
- 當前最佳 metric + 完整 commit hash
- 最近 N 次迭代的 results（摘要：metric、status、description — 不是完整 diff）
- 只有最近一次 keep 迭代的完整 diff
- Supervisor 自己上次的決策（單一筆，不是完整歷史）
- 當前 evaluate 腳本內容（只在上次 review 後有改動才送，否則跳過）
- 當前 program.md 內容（同上）

Single-shot LLM 呼叫搭配結構化狀態物件 — 不用多輪對話。不管跑了多少次迭代，context 大小都是有界的。

### Supervisor 可執行的動作

Supervisor 輸出結構化 JSON 決策：

```json
{
  "action": "continue | intervene",
  "reasoning": "完整分析觀察到了什麼",
  "quality_assessment": "genuine | mixed | gaming",
  "intervention": {
    "git_strategy": "reset | cherry-pick | revert | branch | none",
    "target_commits": ["完整-40-字元-hash"],
    "evaluate_additions": "要追加到 evaluate 腳本的程式碼區塊",
    "program_md_updates": "program.md 的完整新內容",
    "restart": true
  }
}
```

Commit 識別：supervisor 收到完整 commit hash（透過 `git rev-parse`）。Results log 儲存短 hash 用於顯示，但 supervisor 操作前會解析成完整 hash。

可用 git 操作：
- `reset` — 回到特定好 commit，丟棄之後的所有東西
- `cherry-pick` — 從歷史中挑特定好 commit（包括 `failed/` tags 上的）到當前 HEAD
- `revert` — 撤銷特定壞 commit，保留其餘
- `branch` — 從特定位置建立新分支

Supervisor 根據好壞 commit 的分佈決定用哪種策略。

### Evaluate 修改限制

Supervisor 只能對 evaluate 做追加修改。強制機制：**行級 superset 檢查。**

Supervisor 寫完修補後：
1. 將修補前和修補後的 evaluate 拆成行
2. 驗證修補前版本的每一行都存在於修補後版本中（保持順序）
3. 如果任何修補前的行在修補後缺失或被修改 → 拒絕，回滾

這個機制與程式語言無關、O(n) 複雜度、無法透過改名變數繞過。Supervisor 只能追加新的行/區塊 — 不能修改或刪除既有程式碼。

通過 superset 檢查後：
1. 語法檢查（`node --check evaluate.ts` 或對應語言的等效指令）— < 1 秒
2. 下一次內層迭代自然產出新 evaluate 下的第一個分數（不需要額外的 sanity check）
3. 凍結 baseline 由 Python 程式碼在每個 round 結束時執行做 drift 偵測

### Results 分隔

每個 supervisor round 使用獨立的 results 檔案：`results-{tag}-round-{N}.jsonl`。

避免修改 `ExperimentRecord`、`ResultsLog.best()` 或 `is_improvement()`。Supervisor 為每個 round 建立新的 `ResultsLog`。好處：
- `best()` 自然限定在當前 round（不同 evaluate 版本）
- 不會混淆跨 round 的 metric 比較
- `ContextAssembler` 可以在 preamble 中包含先前 round 最佳結果的摘要作為參考資訊，不混入主要歷史

Orchestrator 的 `results_path` 由 supervisor loop 在每個 round 開始時設定。

### 錯誤處理

| 失敗情境 | 回退策略 |
|---------|---------|
| Cherry-pick 合併衝突 | 中止 cherry-pick，回退到 `reset` 最後一個乾淨 commit，記錄警告 |
| Evaluate patch 語法檢查失敗 | 回滾 patch，記錄錯誤，用原始 evaluate 繼續內層迴圈 |
| 凍結 baseline 執行 crash | 記錄 crash 細節，僅用自適應 evaluate 繼續，標記需人工檢視 |
| Supervisor JSON 格式錯誤 | 帶錯誤 context 重試 LLM 呼叫一次；仍然錯誤就記錄並繼續內層迴圈 |
| Supervisor round 超過 max_rounds | 停止 supervisor，讓內層迴圈自主繼續（或完全停止，可設定）|

所有失敗都記錄到 JSONL 和 Markdown supervisor log。

### 日誌

兩種 log 格式，都寫入 `logs/supervisor/`（與其他 log 一起 gitignored）：

**JSONL**（`supervisor-decisions.jsonl`）— 機器可讀的決策：
```json
{
  "round": 1,
  "timestamp": "2026-03-19T12:00:00Z",
  "trigger": "plateau_5_iterations",
  "iterations_reviewed": [6, 7, 8, 9, 10],
  "action": "intervene",
  "quality_assessment": "gaming",
  "git_strategy": "cherry-pick",
  "target_commits": ["abc1234567890...", "def4567890123..."],
  "evaluate_changed": true,
  "program_md_changed": true,
  "baseline_score_before": 49.7,
  "baseline_score_after": 49.7,
  "adaptive_score_before": 55.5,
  "adaptive_score_after": 44.0
}
```

**Markdown**（`round-{N}-review.md`）— 人可讀的完整推理過程：
```markdown
# Supervisor Review — Round 1

## 觸發原因
連續 5 次迭代沒有改善（iter 6-10）

## 分析
Iter 6-8 的 diff 顯示 agent 把 `resolveGroupFolderPath` 複製到
多個檔案來降低 import 數。函數體在 container-runner.ts、
task-scheduler.ts、ipc.ts 三個檔案中完全一樣...

## 決策
介入。Cherry-pick iter 2 和 5（真正的改善）。
在 evaluate 中加入跨檔案函數重複偵測。

## Evaluate 追加內容
[完整追加的程式碼區塊]

## Program.md 更新
[完整改動的 diff]

## 結果
語法檢查：通過
Baseline 分數：49.7 → 49.7（無漂移）
新的自適應 baseline：44.0
以 round 2 重新啟動...
```

### 設定

```yaml
supervisor:
  enabled: false                    # 選擇性啟用
  trigger: "stall"                  # "stall" | "interval"
  stall_threshold: 5               # plateau streak 長度，達到後觸發 review
  review_interval: 5               # 如果 trigger=interval，每 N 次迭代 review
  max_rounds: 3                    # 最多 supervisor 介入輪數
  model: "claude-sonnet-4-6"       # supervisor 用的模型（可以跟內層 agent 不同）
```

### 可重用的既有程式碼

| 元件 | 可重用的既有程式碼 |
|------|-------------------|
| 迭代控制 | `orchestrator.run_one_iteration()` 在 supervisor 迴圈中逐次呼叫 |
| 進度分析 | `PostmortemAnalyzer._build_report()` 取得趨勢數據 |
| LLM 呼叫模式 | `postmortem._call_claude_async()`（CLAUDECODE env 剝離 + SDK）|
| Git 操作 | `GitManager.reset_to_commit()`、`create_branch_from()`、`commit()`；需新增 `cherry_pick(commits)` 和 `revert_commits(commits)` |
| 權限 hooks | `_make_file_hooks()` 搭配 supervisor 專用的 editable/hidden 設定 |
| Results 讀取 | `ResultsLog.read_last(n)`、`ResultsLog.best()` |
| 觸發偵測 | `_count_plateau_streak()`（從尾部計算連續非 keep 紀錄）|
| Config 擴展 | 在 config.py 新增 `SupervisorConfig` dataclass |
| Evaluate 執行 | `ExperimentRunner.execute()` + `parse_metric()` 跑凍結 baseline |
| Commit 解析 | `git rev-parse <short>` 取得完整 hash 用於 cherry-pick |

### Supervisor 不做的事

- 修改 source files（內層 agent 的工作）
- 修改 test files
- 修改 config.yaml 的 metric 定義或權重
- 讀取或修改凍結的 baseline evaluate 檔案
- 修改 results 歷史
- 執行超過 max_rounds 次介入
- 移除或修改既有的 evaluate 程式碼（僅追加）
- 讀取、修改或知道 supervisor_objective.md 的檔案路徑（內容由 Python 程式碼注入 prompt）

## 待決問題

- Supervisor 的模型應該跟內層 agent 不同嗎？（例如內層用 Sonnet 求速度，supervisor 用 Opus 求判斷力）
- 是否需要「人工升級」模式 — supervisor 寫出建議但等人工確認後才執行？
- （已確認）GitManager 需新增 `cherry_pick(commits: list[str])` 和 `revert_commits(commits: list[str])` 方法。目前只有 `reset_to_commit()` 和 `revert_changes()`（回滾所有未 commit 的變更），沒有針對特定 commit 的 cherry-pick 和 revert。
- 如何處理 supervisor 的 evaluate 追加在某些程式碼狀態下出問題的情況？
