# Claude Code CLI vs Agent SDK 可行性分析

**日期：** 2026-03-19
**狀態：** 已擱置（Parked） — 等待觸發條件再執行
**範圍：** 評估在 crucible 中以 Claude Code CLI subprocess 替代 Claude Agent SDK 的可行性

---

## 動機

綜合考量：簡化依賴、提升可靠性、消除 async 複雜度、解決 CLAUDECODE 巢狀衝突 workaround。

## 現狀：Agent SDK 的使用方式

### ClaudeCodeAgent（核心迭代迴圈）

```python
from claude_agent_sdk import query, ClaudeAgentOptions, HookMatcher

options = ClaudeAgentOptions(
    system_prompt=...,
    permission_mode="bypassPermissions",
    allowed_tools=["Read", "Edit", "Write", "Glob", "Grep"],  # 白名單
    model=self.model,
    cwd=workspace,
    hooks=hooks,  # PreToolUse async hooks
)
async for message in query(prompt=prompt, options=options):
    ...
```

- `allowed_tools` 是白名單模式，新工具自動被排除
- PreToolUse hooks 是 in-process Python async 函數，零延遲攔截
- 需要手動 pop `CLAUDECODE` 環境變數避免巢狀衝突
- `asyncio.wait_for()` 做 timeout 控制
- 修改檔案偵測靠 `git diff`，不從 SDK 取得

### Wizard（腳手架生成）

```python
options = ClaudeAgentOptions(
    system_prompt=...,
    permission_mode="bypassPermissions",
    allowed_tools=[],  # 純文字生成
    cwd=Path.cwd(),
)
```

### 已知痛點（已解決）

| 痛點 | 解法 | 狀態 |
|------|------|------|
| CLAUDECODE 巢狀衝突 | `os.environ.pop("CLAUDECODE")` + finally 恢復 | 已解決 |
| `can_use_tool` 強制 AsyncIterable | 改用 `hooks` API | 已解決 |
| async 複雜度 | `asyncio.run()` 包裝 | 已解決 |
| SDK API 不穩定 | 鎖定版本 `>=0.1.6` | 已解決 |

## 方案分析

### 方案 A：全面遷移到 CLI subprocess

```bash
claude -p "optimize..." \
  --system-prompt "..." \
  --allowedTools "Read,Edit,Write,Glob,Grep" \
  --disallowedTools "Bash,WebFetch,WebSearch,Agent,NotebookEdit" \
  --output-format json \
  --max-turns 20 \
  --model sonnet
```

**優點：**
- 移除 SDK 依賴，純 subprocess
- 不需要 CLAUDECODE workaround
- 同步程式碼，無 async
- `--output-format json` 的 `result` 欄位直接取 description
- `--max-turns` 和 `--max-budget-usd` 提供額外控制

**致命缺點：**
- PreToolUse hooks 無法透過 CLI 旗標設定，只能透過 settings.json
- `--allowedTools` 不是白名單（是「不需 prompt 確認」的意思），`--disallowedTools` 是黑名單模式

### 方案 B：核心改用 Anthropic API，自行實作 tool loop

**優點：**
- 完全掌控 tool dispatch，天然支持存取控制
- 精確控制 tool definitions（只送 5 個 tool schema）

**致命缺點：**
- 需要 API key + per-token 計費
- 需要自行實作 Read/Edit/Write/Glob/Grep 的本地執行邏輯（~200-300 行）
- 失去 Claude Code 的內建能力（CLAUDE.md、codebase indexing、git awareness）

### 方案 C3（最佳折衷）：CLI + settings.local.json hooks

**核心設計：**

1. 動態寫入 `<experiment>/.claude/settings.local.json`（gitignored，不侵入 project settings）
2. Hook 指向 crucible 提供的 guard script，從環境變數讀策略
3. Guard script 實作白名單（只允許 Read/Edit/Write/Glob/Grep），deny 其餘所有工具
4. `CRUCIBLE_ACTIVE=1` 環境變數 gate：手動 Claude Code session 不受影響

```json
// .claude/settings.local.json（動態生成）
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/crucible/guard.py"
      }]
    }]
  }
}
```

```bash
CRUCIBLE_ACTIVE=1 \
CRUCIBLE_HIDDEN="opponent.py" \
CRUCIBLE_EDITABLE="solution.py,utils.py" \
claude -p "optimize..." \
  --system-prompt "..." \
  --allowedTools "Read,Edit,Write,Glob,Grep" \
  --disallowedTools "Bash,WebFetch,WebSearch,Agent,NotebookEdit" \
  --output-format json \
  --max-turns 20
```

**Guard script 協議：**
- stdin: `{"tool_name": "Read", "tool_input": {"file_path": "..."}}`
- 檢查 `CRUCIBLE_ACTIVE` → 不存在就 exit 0（allow all）
- 白名單檢查 tool_name → 不在 {Read,Edit,Write,Glob,Grep} 就 exit 2
- Hidden/editable 檢查 → 違規 exit 2 + stderr 寫原因
- 通過 → exit 0

## 殘餘風險評估

### 風險 1：新工具漏列（`--disallowedTools` 黑名單）

- **情境：** Claude Code 更新新增工具，黑名單未列入
- **影響：** 中 — agent 可能用了不該用的工具
- **緩解：** Guard script 白名單兜底，只允許 5 個工具，其餘一律 deny
- **緩解後：** 消除

### 風險 2：手動 session 受 hook 影響

- **情境：** 使用者在實驗目錄開 Claude Code 互動 session，被 guard hook deny
- **影響：** 低 — 不破壞資料，但體驗差
- **緩解：** `CRUCIBLE_ACTIVE` 環境變數 gate，手動 session 不帶此變數，hook 自動 allow all
- **緩解後：** 消除

### 風險 3：Guard script spawn 開銷

- **情境：** 每次 tool call spawn `python3 guard.py`，一次迭代約 30-50 次
- **影響：** 純 stdlib Python 冷啟動 ~50-80ms × 50 次 ≈ 2.5-4 秒/迭代
- **對比：** Agent 思考時間 30-120 秒/迭代，開銷佔 2-8%
- **緩解：** Guard script 只用 stdlib（json, sys, os, pathlib）
- **緩解後：** 可忽略

## 結論：現階段不值得遷移

**核心判斷：** 現有痛點都是「已經被解決的歷史問題」，程式碼已寫好、在跑、有測試。遷移到 CLI 是拿一套已知的 workaround 換成另一套不同的 workaround，淨收益接近零，還要承擔回歸風險。

**具體對比：**

| 面向 | 現有 SDK | 遷移後 CLI |
|------|----------|-----------|
| 工具限制 | 白名單（天然安全） | 黑名單 + hook 白名單兜底（兩層） |
| 即時攔截 | in-process async hook（零延遲） | subprocess hook via settings.local.json（需動態管理） |
| 巢狀衝突 | pop CLAUDECODE（3 行程式碼） | 不需要（但多了 settings.local.json 管理） |
| Async 複雜度 | asyncio.run 包裝（已封裝） | 無（同步 subprocess） |
| 依賴 | claude-agent-sdk | claude CLI（需安裝） |

遷移後並沒有明顯更簡單。

## 觸發條件：何時重新評估

1. **Agent SDK 被 deprecated** — Anthropic 明確表示不再維護
2. **CLI 支援工具白名單旗標** — 例如 `--tools "Read,Edit,Write,Glob,Grep"` 正式穩定
3. **CLI 支援 hook 的 CLI 旗標** — 不需要透過 settings.json 配置
4. **需要 CLI 獨有能力** — MCP server 整合、codebase indexing、CLAUDE.md 自動載入
5. **SDK 出現無法繞過的 breaking change** — 遷移成本低於修復成本

任一條件觸發時，重新評估此文件中的方案 C3。

## 參考資料

- Claude Code CLI 文件：https://code.claude.com/docs/en/cli-reference
- Claude Code headless 模式：https://code.claude.com/docs/en/headless
- Claude Code hooks：https://code.claude.com/docs/en/hooks
- 現有實作：`src/crucible/agents/claude_code.py`
- Hook 協議：stdin JSON → exit code (0=allow, 2=deny) + stderr
