# 設定檔參考

## `.crucible/config.yaml`

```yaml
# 必填欄位
name: "experiment-name"                    # 實驗識別名稱
files:
  editable: ["train.py"]                   # Agent 可以修改的檔案
  readonly: ["data.py"]                    # Agent 可讀但不可修改（選填）
  hidden: ["evaluate.py"]                  # Agent 不可見；subprocess 可用
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
  allow_install: false                     # 允許 agent 透過 requirements.txt 新增套件
  budget:                                  # 成本追蹤
    max_cost_usd: 10.0
    max_cost_per_iter_usd: 0.50
    warn_at_percent: 80
search:                                    # 搜尋策略（選填）
  strategy: "greedy"                       # greedy（預設）| restart | beam
  beam_width: 3                            # beam 模式：獨立分支數量
  plateau_threshold: 8                     # restart + beam：停滯幾次後採取行動
evaluation:                                # 多次執行評估
  repeat: 1                                # 每次迭代執行幾次（1 = 單次）
  aggregation: "median"                    # median | mean
sandbox:                                   # Docker 隔離
  backend: "none"                          # docker | none
  base_image: "python:3.12-slim"
  network: false
  memory_limit: "2g"
  cpu_limit: 2
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

## Eval 指令慣例

eval 指令的輸出必須是 `key: value` 格式，一行一個：

```
metric_name: 0.12345
```

平台會擷取與 `metric.name` 匹配的值。這與常見的 `grep '^loss:' run.log` 模式完全相容。

## 單一指標是刻意的設計

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

## 搜尋策略

控制 crucible 如何探索優化空間。透過頂層的 `search` key 設定。

### `greedy`（預設）

永遠基於目前最佳 commit 繼續。在曲面平滑時效率最高，但長時間運行可能卡在局部最優。

### `restart`

當 `plateau_threshold` 次連續迭代都沒有改善時，重置回初始 baseline commit，並嘗試完全不同的方向——完整歷史保留作為 agent context。

```yaml
search:
  strategy: restart
  plateau_threshold: 8   # 沒有改善幾次後重置
```

### `beam`

維護 `beam_width` 個獨立分支，以輪詢方式循環。每個 beam 都能看到其他 beam 嘗試過的內容，避免重複探索。

```yaml
search:
  strategy: beam
  beam_width: 3          # 維護的分支數量
  plateau_threshold: 8   # beam 模式不使用（每個 beam 有自己的計數器）
```

**注意：** beam 仍是**串行**執行——一次跑一個 agent。總成本與迭代次數成正比，不會乘以 `beam_width`。優勢是探索廣度，不是速度。

**何時使用 beam：** 你有 50+ 次迭代預算，且懷疑搜尋空間有多個不同的局部最優。

## Git 策略

- 每個 session 在一個 branch 上執行：`<branch_prefix>/<tag>`
- 成功的實驗推進 branch（commit 保留）
- 失敗的實驗先打 tag `failed/<tag>/<n>` 再 reset，保留 diff 供事後分析
- `results-{tag}.jsonl` 記錄每次實驗，不論結果如何

## Guard Rails（防護機制）

**Commit 前：** 確認 readonly 檔案未被修改、只有列出的檔案被更動、至少有一個檔案被編輯。

**執行後：** 強制 timeout（SIGTERM → SIGKILL）、指標必須是合法數字（非 NaN/inf）、連續失敗超過 `max_retries` 次自動停止。

## Context 組裝

每次迭代，agent 會收到一份動態組裝的 prompt：

1. **靜態指令** — 來自 `program.md`
2. **當前狀態** — branch、最佳指標、實驗計數
3. **實驗歷史** — 最近結果表格 + 觀察到的模式
4. **行動指示** —「提出並實作一個實驗」
5. **錯誤/crash 上下文** — 如果上一次迭代失敗，錯誤資訊會被包含在內
