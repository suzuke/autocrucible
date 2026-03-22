# Token 分析

追蹤實驗迭代過程中的 prompt 組成、cache 效率和時間分解。

## 快速開始

```bash
# 啟用 profiling 執行實驗
crucible run --tag run1 --profile

# 事後分析 token 使用
crucible postmortem --tag run1 --tokens
```

## 追蹤指標

| 指標 | 來源 | 說明 |
|------|------|------|
| **Prompt 分段佔比** | 組裝時估算 | 每個 prompt 區段的 token 數（instructions、history、state、directive 等） |
| **Cache 命中率** | Claude API | `cache_read / (cache_read + cache_creation)` — prompt 重複使用的比例 |
| **Context 使用率** | Claude API | `input_tokens / context_window_limit` |
| **Agent 耗時** | Wall clock | Claude agent 花費的時間（思考 + 工具使用） |
| **Run 耗時** | Wall clock | 執行實驗的時間（evaluate.py） |
| **SDK 計時** | Agent SDK | SDK 回傳的 `duration_ms` 和 `duration_api_ms` |
| **Turns 數** | Agent SDK | 每輪迭代的 agent 回合數（工具呼叫輪數） |

## 即時輸出

啟用 `--profile` 後，每輪迭代會額外輸出一行分析：

```
[profile] prompt: ~557 tok (instructions: 28%, state: 12%, history: 12%, directive: 41%, preamble: 5%) | cache: 90%
```

## 事後分析

`crucible postmortem --tag run1 --tokens` 顯示：

```
Token Profile (3 iterations)
===========================================================================
 Iter   In Tok  Out Tok  Cache%  Agent(s)  Run(s)   Status
---------------------------------------------------------------------------
    1       44    10868     90%      85.7     4.6     keep
    2       53     5219     94%      43.5    10.2  discard
    3       30     5461     90%      49.4     4.5     keep
---------------------------------------------------------------------------
  avg       42     7182

Prompt Breakdown (avg tokens per section):
             directive:   233 (34%) ███████████
               history:   174 (25%) ████████
          instructions:   157 (22%) ███████
                 state:    87 (12%) ████
              preamble:    33 ( 4%) █

Cache Efficiency: avg 91% hit rate
```

也支援 JSON 輸出：

```bash
crucible postmortem --tag run1 --tokens --json
```

## 觀察重點

**History 成長** — history 區段隨迭代增加（上限為 `agent.context_window.history_limit`，預設 20）。如果它佔比太高，可以在 `config.yaml` 降低上限：

```yaml
agent:
  context_window:
    history_limit: 10
```

**Cache 命中率低** — 如果 cache % 持續偏低，代表 prompt 結構在各輪之間變化太大。靜態區段（instructions、directive）應該會被自動快取。

**Agent vs Run 耗時** — 如果 `Agent(s)` 遠大於 `Run(s)`，瓶頸在 LLM 推理。如果 `Run(s)` 偏高，代表實驗評估本身很慢。

**Input tokens 趨近零** — 在高 cache 命中率下，`In Tok` 只顯示*新的*（未快取）token。這是正常現象，代表快取運作良好。

## 資料儲存

所有分析資料儲存在既有的 `results-{tag}.jsonl` 檔案中。每筆實驗紀錄新增以下欄位：

```json
{
  "agent_duration_seconds": 42.1,
  "run_duration_seconds": 3.2,
  "usage": {
    "input_tokens": 44,
    "output_tokens": 10868,
    "cache_read_input_tokens": 8500,
    "cache_creation_input_tokens": 950,
    "prompt_breakdown": {
      "instructions": 157,
      "state": 68,
      "history": 67,
      "directive": 228,
      "preamble": 33,
      "total": 553
    },
    "sdk_duration_ms": 85000,
    "num_turns": 5
  }
}
```

所有新欄位在未使用 `--profile` 時預設為 `null`，完全向後相容。
