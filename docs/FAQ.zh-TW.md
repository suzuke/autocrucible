# FAQ

## 貪心策略不會卡在局部最優嗎？

Crucible 使用貪心的保留/丟棄迴圈——有改善就保留，沒有就丟棄。聽起來容易卡住，但 LLM agent 跟傳統優化有本質差異：

- Agent 看到**完整歷史**，包含被丟棄和 crash 的嘗試，知道什麼方向走不通、為什麼
- 它能推理失敗原因，刻意嘗試不同的架構方向，而非只做參數微調
- 每次迭代都會讀取實際程式碼，可以做結構性變更——盲目搜索永遠做不到這點

但長時間運行確實有局部最優的風險。Crucible 內建兩種脫困方式：

**`search.strategy: restart`** — 在 `plateau_threshold` 次停滯迭代後自動重置回 baseline commit，並注入完整歷史讓 agent 嘗試完全不同的方向：

```yaml
search:
  strategy: restart
  plateau_threshold: 8   # 沒有改善幾次後重置
```

**`search.strategy: beam`** — 維護 `beam_width` 個獨立分支，以輪詢方式循環。每個分支都能看到其他分支嘗試過什麼，避免重複探索：

```yaml
search:
  strategy: beam
  beam_width: 3
```

**手動多 tag** 也可以完全自行掌控：

```bash
# 從同一 baseline 探索不同方向
crucible run --tag approach-a    # 例如「專注演算法改進」
crucible run --tag approach-b    # 例如「專注底層優化」
crucible compare approach-a approach-b
```

也可以回溯到較早的 commit 重新分支：

```bash
git log crucible/run1              # 找到有潛力的 commit
git checkout <commit>
crucible run --tag run1-variant    # 自動初始化新 branch
```

## 為什麼只支援一個指標？多目標優化怎麼辦？

單一標量指標是刻意的設計選擇，讓保留/丟棄的判斷毫無歧義。多目標的權衡屬於你的 `evaluate.py`，因為你有完整的領域知識來定義「什麼叫更好」。範例請參考[設定檔參考 — 單一指標是刻意的設計](CONFIG.zh-TW.md#單一指標是刻意的設計)。

## 為什麼不平行跑多個 agent？

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

## Agent 修改的程式碼會被執行，這安全嗎？

Agent 能做什麼取決於 **backend**：

- `agent.type: claude-code`（預設、SDK）— 只能用 Read / Edit / Write / Glob / Grep 工具。Agent 沒有 shell 存取權限，無法直接執行任意指令。
- `agent.type: smolagents`（opt-in：`pip install autocrucible[smolagents]`）— 相同五個工具的介面，由 `CheatResistancePolicy` 在 tool boundary 強制；在 `tests/security/` 對抗測試集中目前未觀察到逃逸（可用 `pytest tests/security/ --collect-only` 重新驗證）。
- `agent.type: cli-subscription`（M3，EXPERIMENTAL）— 包裝完整的 agent CLI；CLI 在 host filesystem 上未經 sandbox 執行，Crucible 的 ACL **無法**約束它。需要雙旗 opt-in。詳見 [CLI-SUBSCRIPTION-BACKEND.md](CLI-SUBSCRIPTION-BACKEND.md)。

不論哪個 backend，agent 寫入 editable 檔案的程式碼**都會**被 `commands.run` 執行。如果 editable 檔案能發網路請求、刪除檔案或執行其他危險操作，guard rails 擋不住。

**緩解措施：**

- **縮小 editable 檔案的範圍。** 如果 `sort.py` 只包含一個排序函式，即使 agent 寫了壞程式碼，影響範圍也很有限。
- **評估碼務必設為 `hidden` 而非 `readonly`。** Readonly 檔案 agent 仍可讀取——它**會**研究實作細節（固定種子、評分公式、測試資料）來鑽漏洞。在 `optimize-regression` 範例中，agent 讀取 `evaluate.py` 後找到 `seed=42`，重建了確切的雜訊向量，3 次迭代就達到 MSE=0.0——它記住了測試集而非學會回歸。Hidden 檔案在 agent 執行期間不可存取，但 subprocess 執行實驗時可用。
- **設定 `constraints.timeout_seconds`** 來終止失控的實驗。
- **使用 Docker sandbox**（`sandbox.backend: "docker"`）。容器以 `network=none`、`read_only_root=True`、`cap_drop=["ALL"]`、`pids_limit=128`、非 root 使用者執行（spec §INV-2 預設配置）— 由 `crucible/sandbox.py` 強制。
- **檢查 git log。** 每個變更都有 commit——你可以審計 agent 做了什麼。

這跟 CI/CD 是同樣的信任模型：你審查程式碼，系統執行它。Crucible 只是把迭代迴圈自動化了。

## Web Dashboard 在哪？

沒有——這是刻意的。`results-{tag}.jsonl` 是結構化的 JSONL 檔案，任何工具都能讀，而實驗通常跑幾十次，不是幾千次。全功能 Web UI 是另一個專案級別的工作量，收益有限。

**即時監控**（另開終端）：

```bash
watch -n 5 crucible status
watch -n 5 crucible history --last 10
```

**快速趨勢圖：**

```bash
# 用 jq 擷取指標
crucible history --format jsonl | jq -r '.metric_value'

# 或用 Python
python3 -c "
import json, sys
for line in open('results-run1.jsonl'):
    r = json.loads(line)
    bar = '#' * int(r['metric_value'] / 10)
    print(f'{r[\"iteration\"]:3d} {r[\"metric_value\"]:8.2f} {bar}')
"
```

**程式化存取：**

```bash
crucible status --json | jq .
crucible history --format jsonl | jq '.metric_value'
```
