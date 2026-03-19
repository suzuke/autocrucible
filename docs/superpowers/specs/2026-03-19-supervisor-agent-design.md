# Supervisor Agent 設計（精簡版）

## 問題

Crucible 的 agent 在前幾次迭代做真正的改善，之後發現 gaming 策略刷分。沒有 supervisor 的情況下，20 次迭代可能只有 5 次有價值，剩下 15 次是垃圾。人工要翻所有 diff 才能分辨好壞。

## Supervisor 的角色

**自動 code reviewer + 早期回滾機制。** 不改 evaluate，不停下來等人。

偵測到 gaming → reset 到最後 genuine commit → 更新 program.md 警告 → 繼續跑。

不假裝能解決 gaming 問題本身。gaming 模式無限多，堵不完。Supervisor 是 best-effort 篩選器：抓到的就賺到了，沒抓到的不比沒有 supervisor 差。

## 設計

### 架構

```
crucible run（supervisor 開啟）
  │
  SupervisorLoop（掌控迭代節奏）
  │
  ├─ 呼叫 orchestrator.run_one_iteration() × N 次
  │
  ├─ 觸發條件達成 → supervisor review
  │   ├─ 讀最近 N 次迭代的 results + 最近 keep 的 diff
  │   ├─ 判斷：genuine / gaming
  │   │
  │   ├─ genuine → 繼續跑
  │   └─ gaming →
  │       ├─ 找最後一個 genuine commit
  │       ├─ git reset 到該 commit
  │       ├─ 更新 program.md（警告偵測到的 gaming 模式）
  │       ├─ 在 context 加入回滾訊息
  │       └─ 繼續跑
  │
  └─ 到 max_rounds 停止介入，剩餘迭代自然跑完
```

### Supervisor 做的事

1. **Review** — 讀最近幾次迭代的 diff，判斷是 genuine 還是 gaming
2. **Reset** — 偵測到 gaming 就 reset 到最後 genuine commit
3. **Cherry-pick** — 如果好壞交錯，從歷史中挑好的 commit
4. **更新 program.md** — 警告偵測到的 gaming 模式，引導 agent 換方向
5. **記錄** — 完整的思考過程和決策寫入 log

### Supervisor 不做的事

- 不修改 evaluate（metric 定義是用戶的事）
- 不修改 source files（那是內層 agent 的事）
- 不修改 test files
- 不修改 config.yaml
- 不停下來等人
- 不修改 results 歷史

### 權限模型

| 檔案 | 內層 agent | Supervisor LLM |
|------|-----------|----------------|
| Source files | 讀 + 寫 | 僅讀 |
| Test files | 僅讀 | 僅讀 |
| evaluate 腳本 | hidden | 僅讀（可看但不能改）|
| .crucible/program.md | 僅讀 | 讀 + 寫 |
| supervisor_objective.md | hidden | hidden（Python 注入 prompt）|
| results-*.jsonl | N/A | 僅讀 |
| git history / diffs | N/A | 讀 |
| logs/supervisor/ | N/A | 寫（gitignored）|

### Supervisor 核心目標

檔案：`.crucible/supervisor_objective.md`，用戶在實驗前撰寫。

定義「什麼算 genuine、什麼算 gaming」的判斷標準。對兩個 agent 都是 hidden，Python 程式碼在每次 review 時注入 supervisor 的 system prompt。

沒有提供時使用內建預設：「確保改動是真正的品質改善而非 metric gaming。搬移程式碼（原始位置刪除）是合理的，複製程式碼（原始位置還在）是 gaming。」

### 觸發條件

使用 `_count_plateau_streak()`：連續 K 次非 keep 時觸發 review。

也可設定為固定間隔。

### Supervisor review 輸入

- 當前最佳 metric + 完整 commit hash
- 最近 N 次迭代的 results 摘要
- 最近 keep 迭代的完整 diff
- Supervisor 上次的決策（單筆）
- 當前 program.md 內容

Single-shot LLM 呼叫，context 有界。

### Supervisor 輸出

```json
{
  "action": "continue | rollback",
  "reasoning": "完整分析",
  "quality_assessment": "genuine | mixed | gaming",
  "gaming_pattern": "描述偵測到的 gaming 模式（如果有）",
  "rollback_to": "commit hash（如果 rollback）",
  "cherry_pick": ["commit hash list（如果需要挑選）"],
  "program_md_warning": "要加入 program.md 的警告文字"
}
```

### 失誤容忍

| 失誤類型 | 後果 | 防線 |
|---------|------|------|
| False positive（誤判 genuine 為 gaming）| 丟掉好 commit，浪費幾次迭代 | max_rounds 限制回滾次數 |
| False negative（沒抓到 gaming）| 跟沒有 supervisor 一樣 | 不比原來差 |
| 反覆回滾死循環 | 浪費迭代 | max_rounds 到上限自動停止介入 |

### 日誌

**JSONL**（`logs/supervisor/supervisor-decisions.jsonl`）— 機器可讀：
```json
{
  "round": 1,
  "timestamp": "2026-03-19T12:00:00Z",
  "trigger": "plateau_5_iterations",
  "iterations_reviewed": [6, 7, 8, 9, 10],
  "action": "rollback",
  "quality_assessment": "gaming",
  "gaming_pattern": "cross-file function duplication",
  "rollback_to": "abc1234...",
  "program_md_updated": true
}
```

**Markdown**（`logs/supervisor/round-{N}-review.md`）— 完整推理過程：
```markdown
# Supervisor Review — Round 1

## 觸發原因
連續 5 次沒有改善

## 分析
Iter 12-14 的 diff 顯示 resolveGroupFolderPath 被複製到三個檔案...

## 決策
回滾到 iter 11。更新 program.md 警告 cross-file duplication。

## program.md 新增警告
DO NOT copy functions across files to reduce import count...
```

### 設定

```yaml
supervisor:
  enabled: false                    # 選擇性啟用
  trigger: "stall"                  # "stall" | "interval"
  stall_threshold: 5               # plateau streak 長度
  review_interval: 5               # trigger=interval 時每 N 次 review
  max_rounds: 3                    # 最多回滾次數
  model: "claude-sonnet-4-6"       # supervisor 模型
```

### 模組結構

新檔案：`supervisor.py`，包含 `SupervisorLoop` class。

```python
# cli.py
if config.supervisor.enabled:
    loop = SupervisorLoop(orchestrator, config.supervisor)
    loop.run()
else:
    orchestrator.run_loop()
```

`SupervisorLoop` 呼叫 `orchestrator.run_one_iteration()` 逐次執行，在觸發點插入 review。Orchestrator 不改動。

### 可重用的既有程式碼

| 元件 | 重用 |
|------|------|
| 迭代控制 | `orchestrator.run_one_iteration()` |
| LLM 呼叫 | `postmortem._call_claude_async()` 模式 |
| Git 操作 | `GitManager.reset_to_commit()`、`create_branch_from()`；需新增 `cherry_pick()` |
| Results 讀取 | `ResultsLog.read_last(n)` |
| 觸發偵測 | `_count_plateau_streak()` |
| Config | 新增 `SupervisorConfig` dataclass |

### Results 分隔

每次回滾後用新的 results 檔案：`results-{tag}-round-{N}.jsonl`。避免跨 round 的分數混淆。

## 設計原則

1. **Supervisor 不優化任何東西** — 它只篩選和回滾
2. **evaluate 是用戶的 ground truth** — 兩個 agent 都不能改
3. **不停下來等人** — 全程自主運行
4. **失誤不致命** — false positive 浪費迭代，false negative 不比原來差
5. **max_rounds 是硬上限** — 防止死循環

## 待決問題

- Supervisor 模型應該跟內層 agent 不同嗎？
- GitManager 需新增 `cherry_pick(commits)` 方法
- 回滾後 context 怎麼呈現歷史？（需要讓 agent 知道被回滾了，以及為什麼）
