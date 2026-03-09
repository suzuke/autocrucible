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
# 安裝為全域 CLI 工具
uv tool install crucible

# 或從本地 clone 安裝
git clone https://github.com/user/crucible.git
uv tool install ./crucible
```

驗證：

```bash
crucible --help
```

### 更新

```bash
# 從 PyPI
uv tool install crucible --force

# 從本地原始碼（pull 最新後）
uv tool install ./crucible --force
```

### 開發模式

```bash
git clone https://github.com/user/crucible.git
cd crucible
uv sync                 # 安裝到本地 .venv
uv run crucible --help  # 從原始碼執行
uv run pytest           # 執行測試
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
crucible init --tag run1   # 自動 git-init（如果還沒有）
```

**從零開始：**

```bash
crucible new ~/my-experiment
cd ~/my-experiment
# 編輯 .crucible/config.yaml 和 program.md
crucible init --tag run1   # 自動 git-init（如果還沒有）
```

如果實驗需要第三方套件（numpy、torch 等），會列在產生的 `pyproject.toml` 中。安裝它們：

```bash
uv sync
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

這會建立 git branch `crucible/run1` 並初始化 `results.tsv`。如果專案還不是 git repo，`init` 會自動執行 `git init`、暫存所有檔案並建立初始 commit。

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

如果中斷了，直接重新執行同一指令——crucible 會自動偵測既有 branch 並從上次狀態繼續：

```bash
crucible run --tag run1   # 自動恢復先前進度
```

### 4. 查看結果

```bash
crucible status
# Experiment: optimize-sorting
# Total: 15  Kept: 8  Discarded: 5  Crashed: 2
# Best ops_per_sec: 142000.0 (commit b2c3d4e)

crucible history --last 5
# Commit      Metric Status   Description
# ------------------------------------------------------------
# b2c3d4e   142000.0 keep     switch to radix sort for large arrays
# a1b2c3d   138000.0 keep     add insertion sort for small partitions
# ...

# JSON 輸出，方便程式化使用
crucible status --json
crucible history --json --last 20

# 比較兩個實驗
crucible compare run1 run2
crucible compare run1 run2 --json
```

## 運作原理

```
crucible run --tag run1
        │
        ▼
┌─────────────────────────────────┐
│  1. 組裝 prompt                  │  指令 + 歷史 + 狀態
│  2. Claude Agent SDK             │  agent 讀取/編輯檔案
│  3. Guard rails                  │  驗證編輯合規
│  4. Git commit                   │  快照變更
│  5. 執行實驗                      │  python evaluate.py > run.log
│  6. 解析指標                      │  grep '^metric:' run.log
│  7. 保留或丟棄                    │  改善? 保留 : reset
│  8. 循環                         │
└─────────────────────────────────┘
```

- **Agent**：使用 [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)，搭配工具白名單（Read、Edit、Write、Glob、Grep）。Agent 可以讀取檔案、精準編輯和搜尋程式碼——但不能執行任意指令。
- **執行環境**：如果專案有 `.venv/`，crucible 會自動啟用它來執行實驗指令，確保 `python3 evaluate.py` 使用正確的直譯器和套件。
- **Git**：每次嘗試都會 commit。改善就推進 branch；失敗則打 tag 後 reset，保留 diff 供事後分析。

### 執行前驗證

```bash
crucible validate
#   [PASS] Config: config.yaml is valid
#   [PASS] Instructions: .crucible/program.md exists
#   [PASS] Editable files: All files exist
#   [PASS] Run command: Executed successfully
#   [PASS] Eval/metric: ops_per_sec: 42000.0
```

### 詳細 log 輸出

```bash
crucible -v run --tag run1   # debug 級別輸出
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
  max_retries: 3                           # 連續失敗上限，超過即停止
agent:
  type: "claude-code"                      # Agent 後端
  instructions: "program.md"              # 靜態指令檔
  system_prompt: "system.md"              # 自訂系統提示詞（選填，預設使用內建）
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

### 單一指標是刻意的設計

Crucible 使用單一標量指標——這是刻意的設計選擇，不是缺陷。單一數字讓保留/丟棄的判斷毫無歧義，保持迴圈簡單可靠，並迫使你在評估碼中明確定義「什麼叫更好」。

**多目標優化**應在 `evaluate.py` 中處理，而非平台層：

```python
latency = measure_latency()
throughput = measure_throughput()

# 加權組合
metric = throughput / latency

# 約束式（違反約束就歸零）
metric = throughput if latency < 100 else 0

# 分階段（先保證正確性，再優化效能）
metric = throughput if correctness == 1.0 else -1000

print(f"metric: {metric}")
```

把複雜度放在你的領域邏輯裡（它本來就屬於那裡），而不是平台裡。

### Git 策略

- 每個 session 在一個 branch 上執行：`<branch_prefix>/<tag>`
- 成功的實驗推進 branch（commit 保留）
- 失敗的實驗先打 tag `failed/<tag>/<n>` 再 reset，保留 diff 供事後分析
- `results.tsv` 記錄每次實驗，不論結果如何

### Guard Rails（防護機制）

**Commit 前：** 確認 readonly 檔案未被修改、只有列出的檔案被更動、至少有一個檔案被編輯。

**執行後：** 強制 timeout（SIGTERM → SIGKILL）、指標必須是合法數字（非 NaN/inf）、連續失敗超過 `max_retries` 次自動停止。

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
| `optimize-compress` | `compression_ratio` | maximize | 無損文字壓縮（禁用 zlib/gzip） |
| `optimize-gomoku` | `win_rate` | maximize | AlphaZero 風格五子棋 agent 訓練 |

### 範例展示：optimize-compress

一個展示 crucible 效果的範例——agent 從零開始建構無損文字壓縮器：

```bash
crucible new ~/compress -e optimize-compress
cd ~/compress
crucible init --tag run1
crucible run --tag run1
```

從 baseline RLE 壓縮器（0.51x——比不壓還差）出發，agent 通常會：
- **Iter 1**：實作 LZ77 + Huffman → ~2.63x
- **Iter 2**：加入最佳解析 DP + symbol remapping → ~2.81x（超越 zlib 的 2.65x）
- **Iter 3+**：上下文建模、算術編碼 → 3.0x+

## 專案結構

```
my-experiment/
├── .crucible/
│   ├── config.yaml     # 做什麼、怎麼跑、量什麼
│   └── program.md      # 給 LLM agent 的指令
├── solution.py          # Agent 修改的程式碼（editable）
├── evaluate.py          # 固定的評估碼（readonly）
├── pyproject.toml       # 實驗依賴（不含 crucible 本身）
├── results.tsv          # 自動產生的實驗紀錄
└── run.log              # 最新一次實驗輸出
```

Crucible 安裝為**全域 CLI 工具**——它不是你實驗專案的依賴。專案的 `pyproject.toml` 只列出實驗所需的套件（numpy、torch 等）。

## Claude Code Skill：互動式建立專案

Crucible 附帶一個 [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill，提供互動式引導工作流程，從零開始建立實驗專案。

### 安裝 Skill

```bash
# 複製 skill 到你的 Claude Code skills 目錄
cp -r /path/to/crucible/.claude/skills/crucible-setup ~/.claude/skills/
```

或者，如果你 clone 了 crucible repo，可以加到專案的 `.claude/` 目錄：

```bash
mkdir -p .claude/skills
cp -r /path/to/crucible/.claude/skills/crucible-setup .claude/skills/
```

### 使用方式

安裝後，直接告訴 Claude Code 你想優化什麼：

```
> 我想優化一個矩陣乘法演算法
> 幫我建立一個最大化推理吞吐量的實驗
> 為我的排序實作建立 benchmark
```

Claude Code 會自動啟用 `crucible-setup` skill，引導你完成 7 步驟工作流程：

1. **定義指標** — 量什麼、方向（最小化/最大化）、依賴套件
2. **架構約束** — 如果你要求特定方法，skill 會在 `evaluate.py` 中用程式碼強制（而非只靠 prompt），防止 [Goodhart's Law](https://en.wikipedia.org/wiki/Goodhart%27s_law) 問題
3. **建立評估碼** — readonly 的 `evaluate.py`，含正確性閘門和方法驗證
4. **建立 baseline** — 簡單但正確的起始實作
5. **撰寫 agent 指令** — `program.md`，區分硬規則（程式碼強制）與軟規則（建議）
6. **撰寫 config.yaml** — 指標、指令、timeout、防護機制
7. **驗證 baseline** — 實際跑一次確認一切正常

### 何時用 Skill vs 範例？

| 方式 | 適用場景 |
|------|----------|
| `crucible new -e <範例>` | 標準問題，類似內建範例 |
| Claude Code skill | 自訂問題、獨特指標、架構約束 |

Skill 在有**架構約束**時特別有價值（例如「必須使用神經網路」、「用 MCTS 實作」）。它會在評估碼中產生 `verify_method()` 檢查，如果 agent 放棄指定架構就歸零指標——否則你得自己手寫這些驗證。

## FAQ

### 貪心策略不會卡在局部最優嗎？

Crucible 使用貪心的保留/丟棄迴圈——有改善就保留，沒有就丟棄。聽起來容易卡住，但 LLM agent 跟傳統優化有本質差異：

- Agent 看到**完整歷史**，包含被丟棄和 crash 的嘗試，知道什麼方向走不通、為什麼
- 它能推理失敗原因，刻意嘗試不同的架構方向，而非只做參數微調
- 每次迭代都會讀取實際程式碼，可以做結構性變更——盲目搜索永遠做不到這點

但長時間運行確實有局部最優的風險。內建的脫困方式是**多 tag**——本質上是手動的 beam search：

```bash
# 從同一 baseline 探索不同方向
crucible init --tag approach-a
crucible init --tag approach-b
crucible run --tag approach-a    # 例如「專注演算法改進」
crucible run --tag approach-b    # 例如「專注底層優化」
crucible compare approach-a approach-b
```

也可以回溯到較早的 commit 重新分支：

```bash
git log crucible/run1              # 找到有潛力的 commit
git checkout <commit>
crucible init --tag run1-variant   # 從那個點開新分支
crucible run --tag run1-variant
```

### 為什麼只支援一個指標？多目標優化怎麼辦？

參見上方的[單一指標是刻意的設計](#單一指標是刻意的設計)。單一標量指標讓保留/丟棄的判斷毫無歧義。多目標的權衡屬於你的 `evaluate.py`，因為你有完整的領域知識來定義「什麼叫更好」。

### 為什麼不平行跑多個 agent？

Crucible 每個 tag 串行跑一個 agent。這是刻意的：

- **成本效率**：平行 agent 成倍增加 API 費用，但串行 agent 會從歷史學習——第 N+1 次迭代比第 N 次更聰明，因為它看到了什麼有效、什麼無效。盲目平行探索沒有這個優勢。
- **簡單性**：平行 agent 修改同一份檔案會造成 git 衝突。解決這個需要 worktree 隔離、結果同步和合併策略——大量複雜度換取有限收益。

**手動方式已涵蓋大部分需求。** 在不同終端跑多個 tag：

```bash
# Terminal 1                        # Terminal 2
crucible run --tag algo-focus       crucible run --tag lowlevel-focus
```

每個 tag 是獨立的實驗分支。完成後比較結果：

```bash
crucible compare algo-focus lowlevel-focus
```

你可以完全掌控哪些方向值得平行投入，零額外複雜度。

### Agent 修改的程式碼會被執行，這安全嗎？

Agent 無法執行任意指令——它只能使用 Read、Edit、Write、Glob、Grep 工具。但它寫入 editable 檔案的程式碼**會**被 `commands.run` 執行。如果 editable 檔案能發網路請求、刪除檔案或執行其他危險操作，guard rails 擋不住。

**緩解措施：**

- **縮小 editable 檔案的範圍。** 如果 `sort.py` 只包含一個排序函式，即使 agent 寫了壞程式碼，影響範圍也很有限。
- **讓評估碼（readonly）以受控方式 import 並呼叫 editable 程式碼。** Agent 無法修改 `evaluate.py`。
- **設定 `constraints.timeout_seconds`** 來終止失控的實驗。
- **在容器或 VM 中執行**（針對不信任的工作負載）。Crucible 不需要 root 或網路存取。
- **檢查 git log。** 每個變更都有 commit——你可以審計 agent 做了什麼。

這跟 CI/CD 是同樣的信任模型：你審查程式碼，系統執行它。Crucible 只是把迭代迴圈自動化了。
