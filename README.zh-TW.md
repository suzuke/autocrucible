# crucible

繁體中文 | [English](README.md)

通用自主實驗平台。定義要編輯的檔案、執行指令和評估指標，然後讓 LLM agent 無限迭代來優化你的指標。

## 前置需求

- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** — Python 套件管理器
  ```bash
  # macOS / Linux
  curl -LsSf https://astral.sh/uv/install.sh | sh

  # 或透過 Homebrew
  brew install uv
  ```
- **Git** — 平台使用 git 管理實驗版本
- **[Claude Code](https://docs.anthropic.com/en/docs/claude-code)** — 需安裝 `claude` CLI 並完成認證
  ```bash
  # 安裝
  npm install -g @anthropic-ai/claude-code

  # 認證（依照提示操作）
  claude
  ```

## 安裝

```bash
uv sync
```

安裝完成後會建立 `crucible` CLI 指令。驗證：

```bash
crucible --help
```

## 快速開始

### 1. 建立專案

**從範例建立：**

```bash
# 列出可用範例
crucible new . --list

# 從範例建立
crucible new ~/my-experiment -e optimize-sorting
cd ~/my-experiment
git init && git add -A && git commit -m 'initial'
```

**從零開始：**

```bash
crucible new ~/my-experiment
cd ~/my-experiment
# 編輯 .crucible/config.yaml 和 program.md
git init && git add -A && git commit -m 'initial'
```

**或手動設定** — 在專案 repo 中建立 `.crucible/config.yaml`：

```yaml
name: "optimize-sorting"
description: "找到最快的排序實作"

files:
  editable:
    - "sort.py"
  readonly:
    - "benchmark.py"

commands:
  run: "python benchmark.py > run.log 2>&1"
  eval: "grep '^ops_per_sec:' run.log"

metric:
  name: "ops_per_sec"
  direction: "maximize"
```

以及 `.crucible/program.md`，寫給 agent 的指令：

```markdown
你正在優化一個排序演算法。
編輯 sort.py 來提升以 ops_per_sec 衡量的吞吐量。
嘗試不同的演算法、資料結構和優化方式。
```

### 2. 初始化

```bash
crucible init --tag run1
```

這會建立 git branch `crucible/run1` 並初始化 `results.tsv`。

### 3. 執行

```bash
crucible run --tag run1
```

平台會無限循環執行：
1. 要求 agent 提出並實作一個變更
2. 驗證編輯（只允許修改指定檔案）
3. Commit 並執行實驗
4. 解析指標
5. 有改善就保留，沒有就丟棄
6. 重複

按 `Ctrl+C` 優雅停止（會等當前實驗完成）。

### 4. 查看結果

```bash
crucible status
# Total: 15  Kept: 8  Discarded: 5  Crashed: 2
# Best ops_per_sec: 142000.0 (commit b2c3d4e)

crucible history --last 5
# Commit      Metric Status   Description
# ------------------------------------------------------------
# b2c3d4e   142000.0 keep     switch to radix sort for large arrays
# a1b2c3d   138000.0 keep     add insertion sort for small partitions
# ...
```

## 設定檔參考

### `.crucible/config.yaml`

```yaml
# 必填欄位
name: "experiment-name"                    # 實驗識別名稱
files:
  editable: ["train.py"]                   # Agent 可以修改的檔案
  readonly: ["eval.py"]                    # Agent 不可修改的檔案（選填）
commands:
  run: "python train.py > run.log 2>&1"    # 執行一次實驗的指令
  eval: "grep '^metric:' run.log"          # 擷取指標的指令
metric:
  name: "metric"                           # 指標名稱（對應 eval 輸出的 key）
  direction: "minimize"                    # "minimize" 或 "maximize"

# 選填欄位（以下為預設值）
description: ""                            # 人類可讀的描述
commands:
  setup: "pip install -r requirements.txt" # 一次性初始化指令（init 時執行）
constraints:
  timeout_seconds: 600                     # 超過此秒數強制終止實驗
  max_retries: 3                           # crash/violation 連續失敗上限
agent:
  type: "claude-code"                      # Agent 後端
  instructions: "program.md"              # 靜態指令檔
  context_window:
    include_history: true                  # 注入過去實驗結果
    history_limit: 20                      # prompt 中最多帶幾筆歷史
    include_best: true                     # 顯示目前最佳指標
git:
  branch_prefix: "crucible"                # Branch 名稱：<prefix>/<tag>
  tag_failed: true                         # 失敗實驗打 tag 保留 diff
```

### Eval 指令慣例

eval 指令的輸出必須是 `key: value` 格式，一行一個：

```
metric_name: 0.12345
```

平台會擷取與 `metric.name` 匹配的值。這與常見的 `grep '^loss:' run.log` 模式完全相容。

### Git 策略

- 每個 session 在一個 branch 上執行：`<branch_prefix>/<tag>`
- 成功的實驗推進 branch（commit 保留）
- 失敗的實驗先打 tag `failed/<tag>/<n>` 再 reset，保留 diff 供事後分析
- `results.tsv` 記錄每次實驗，不論結果如何

### Guard Rails（防護機制）

**Commit 前：** 確認 readonly 檔案未被修改、只有列出的檔案被更動、至少有一個檔案被編輯。

**執行後：** 強制 timeout（SIGTERM → SIGKILL）、指標必須是合法數字（非 NaN/inf）、連續失敗超過 max_retries 次自動停止。

### Context 組裝

每次迭代，agent 會收到一份動態組裝的 prompt：

1. **靜態指令** — 來自 `program.md`
2. **當前狀態** — branch、最佳指標、實驗計數
3. **實驗歷史** — 最近結果表格 + 觀察到的模式
4. **行動指示** —「提出並實作一個實驗」
5. **錯誤/crash 上下文** — 如果上一次迭代失敗，錯誤資訊會被包含在內

## 範例

內建範例，快速開始。從任何範例建立專案：

```bash
crucible new ~/my-project -e <範例名稱>
```

| 範例 | 指標 | 方向 | 說明 |
|------|------|------|------|
| `optimize-sorting` | `ops_per_sec` | maximize | 純 Python 排序吞吐量優化 |
| `optimize-regression` | `val_mse` | minimize | 合成回歸（非線性交互） |
| `optimize-classifier` | `val_accuracy` | maximize | Numpy 手寫神經網路，8 類別分類 |
| `optimize-gomoku` | `win_rate` | maximize | AlphaZero 風格五子棋 agent 訓練 |
