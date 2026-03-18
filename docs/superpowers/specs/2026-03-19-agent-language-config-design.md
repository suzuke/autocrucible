# Agent Language Config

**日期：** 2026-03-19
**狀態：** 已核准
**範圍：** 在 config.yaml 新增 `agent.language` 欄位，控制 agent 回覆描述的語言

---

## 動機

crucible 的 agent 回覆和 results.tsv 的 description 欄位目前固定為英文。非英語母語的使用者在掃讀實驗歷史時，母語描述更容易快速理解。

## 設計

### 影響範圍

只影響 agent 產出的 description 文字。以下維持英文不變：
- context.py 的 prompt 模板（PREAMBLE、status labels、crash diagnosis、directive）
- CLI 輸出訊息
- results.tsv 的 schema（欄位名稱）

### Config schema

`config.yaml` 的 `agent` section 新增 optional `language` 欄位：

```yaml
agent:
  language: "zh-TW"  # optional, 預設 null → 英文
```

合法值：任意語言標識字串（`zh-TW`, `ja`, `ko`, `es` 等）。不做枚舉限制。

### 程式碼變更

**config.py** — `AgentConfig` dataclass 加欄位：

```python
@dataclass
class AgentConfig:
    ...
    language: str | None = None
```

**agents/claude_code.py** — 建構子接收 language，`get_system_prompt()` 尾端追加：

```python
if self.language:
    prompt += f"\n\nWrite ALL your summaries and descriptions in {self.language}."
```

**cli.py** — 傳遞 `config.agent.language` 給 `create_agent()`。

### 測試

加一個測試確認 language 有值時 system prompt 包含該語言字串。

### 不改的東西

- context.py
- SYSTEM_PROMPT 本體
- CLI 輸出
- status labels
- results.tsv schema
- wizard.py
