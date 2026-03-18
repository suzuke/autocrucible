# optimize-humanizer-tw: Skill 優化實驗設計

## 目標

用 crucible 的 generate-edit-evaluate loop 迭代優化 humanizer-tw skill（.md 檔案），使其指導 LLM 產出更自然的中文改寫，同時保持或提升簡潔度。

這是 **Phase 1**（方案 C 的第一步）：純粹一個 crucible 實驗專案，不修改 crucible 核心。Phase 2/3（抽象成可複用框架）待 Phase 1 驗證後再決定。

## 核心概念

將 skill.md 視為「code」— crucible agent 編輯它，evaluate.py 衡量效果。跟優化 sort.py 完全一樣的流程，只是 evaluate.py 裡恰好呼叫 LLM 而非執行演算法。

## 專案結構

```
optimize-humanizer-tw/
├── .crucible/
│   ├── config.yaml         # metric, commands, file policy
│   └── program.md          # agent 指示（readonly）
├── skill.md                # ← agent 編輯這個（editable）
├── evaluate.py             # ← 評估引擎（hidden）
├── test_cases/
│   ├── train.yaml          # agent 可見的範例（readonly）
│   └── test.yaml           # 評分用（hidden，agent 看不到）
├── rubric.md               # LLM judge 評分標準（hidden）
└── .gitignore
```

### 檔案角色

| 檔案 | 類別 | 用途 |
|------|------|------|
| `skill.md` | editable | 被優化的 humanizer-tw skill 內容 |
| `test_cases/train.yaml` | readonly | agent 可參考的範例測試 |
| `test_cases/test.yaml` | hidden | 實際評分用的測試集 |
| `evaluate.py` | hidden | 評估引擎（LLM 呼叫 + 評分） |
| `rubric.md` | hidden | LLM judge 的評分標準 |
| `.crucible/program.md` | readonly | agent 行為指示 |

## 評估流程

evaluate.py 執行以下步驟：

1. **讀取** agent 修改後的 `skill.md`
2. **結構檢查**（長度上限、必要段落存在）→ 不合格直接 metric = 0
3. **對每個 test case**：
   - `claude -p "{ai_text}" -s "{skill.md content}"` — 讓 skill 處理測試文本（`-p` = prompt, `-s` = system prompt，需在實作時確認最新 CLI flag）
   - 擷取改寫輸出
   - `claude -p "judge: {original} → {rewritten}" -s "{rubric.md}"` — LLM judge 打分
   - 解析 5 維度分數
4. **規則覆蓋檢查** — regex 偵測 AI 模式殘留
5. **聚合** — 計算 final metric

## Metric 公式

三個子分數 + 結構罰分：

```
quality_score     = avg(judge 5維度打分)             # 0~50
coverage_score    = (消除的模式數 / 應消除總數) × 20  # 0~20
brevity_bonus     = max(0, 20 - 20 * (ratio - 0.5)) # 0~20
structure_penalty = 結構違規扣分                     # -10~0

raw = quality_score + coverage_score + brevity_bonus + structure_penalty
final_metric = max(0, raw)  # clamp to 0
```

### 權重分配

| 維度 | 範圍 | 佔比 | 說明 |
|------|------|------|------|
| 品質 | 0-50 | ~55% | LLM judge 5 維度（直接性/節奏/信任度/真實性/精煉度） |
| 覆蓋率 | 0-20 | ~22% | AI 模式消除比例（regex 檢測） |
| 簡潔度 | 0-20 | ~22% | 越短越好，ratio ≤ 0.5 = 滿分，> 1.5 = fail |
| 結構罰分 | -10~0 | 硬門檻 | 超長 / 缺段落 / placeholder |

理論上限 90 分，實際 55-70 就是好成績。direction: maximize。

### Brevity Bonus 曲線

```
ratio = current_len / original_len

ratio ≤ 0.5 → 20 (砍半還同樣好 = 滿分)
ratio = 0.7 → 15
ratio = 1.0 → 10 (跟原來一樣長 = 基礎分)
ratio = 1.2 →  5
ratio = 1.5 →  0
ratio > 1.5 → FAIL (metric = 0)
```

設計意圖：agent 面臨 trade-off — 加範例可能提高品質和覆蓋率，但會降低簡潔度。要找到「最精煉的表達方式」才能同時贏。

## Test Case 格式

```yaml
# test.yaml / train.yaml 共用格式
- id: tech_blog_01
  genre: 科技部落格
  input: |
    隨著人工智慧技術的快速發展，越來越多的企業
    開始採用 AI 解決方案。此外，這不僅僅是技術
    的進步，更是理念的革新。讓我們拭目以待。
  expected_patterns:  # 應被消除的 AI 模式
    - 時代開場白
    - 連接詞濫用
    - 否定式排比
    - 展望類結尾
  tone: 隨意
```

### 測試集設計

- **train.yaml** (readonly, ~3 cases)：agent 可見，涵蓋不同文體
- **test.yaml** (hidden, ~5 cases)：評分用，agent 不可見。涵蓋：科技部落格、社群貼文、技術文件、散文、商業文案

## Anti-Goodhart 機制

| 機制 | 防止什麼 |
|------|----------|
| test.yaml = hidden | agent 無法針對測試文本塞答案 |
| skill.md 長度上限 (1.5x) | 防止膨脹堆砌 |
| 必要段落檢查 | 防止刪除「個性與靈魂」等核心段落 |
| rubric.md = hidden | agent 不知道打分細節 |
| brevity bonus | 鼓勵精煉而非堆砌 |
| 條件邏輯禁令 (program.md) | 防止「如果看到 X 就輸出 Y」的作弊 |

## config.yaml

```yaml
name: optimize-humanizer-tw
description: 優化中文去 AI 味 skill 的改寫品質

files:
  editable: [skill.md]
  readonly: [test_cases/train.yaml]
  hidden: [evaluate.py, test_cases/test.yaml, rubric.md]

commands:
  run: python3 -u evaluate.py
  eval: python3 -u evaluate.py --print-metric

metric:
  name: score
  direction: maximize

constraints:
  timeout_seconds: 300
  max_retries: 3
  max_iterations: 20
```

## program.md 重點

### 目標
改進 skill.md 使其指導 LLM 產出更自然的中文改寫。

### 允許
- 改善規則描述的清晰度和優先順序
- 增加或修改範例（改寫前/後對照）
- 調整段落結構、分類方式
- 新增 skill 未覆蓋的 AI 模式規則

### 禁止
- 刪除「個性與靈魂」段落
- 刪除「品質評分」段落
- 讓 skill.md 超過原始長度的 1.5 倍
- 加入「如果看到特定文字就...」的條件邏輯

### 評分維度（透明）
品質（直接性/節奏/信任度/真實性/精煉度）+ 規則覆蓋率 + 簡潔度 - 結構違規

## 預估

| 項目 | 估計值 |
|------|--------|
| 每次評估 | ~5 test cases × 2 LLM calls = 10 calls |
| 每次迭代 | ~2-3 分鐘 |
| 建議 iterations | 15-20 |
| 總時間 | 45-60 分鐘 |

## Phase 2/3 展望（暫不實作）

- **Phase 2**：從 evaluate.py 抽取通用模式（LLM-judge、test case loader、rubric parser）
- **Phase 3**：整合進 crucible 核心成為新 experiment type

決定是否推進 Phase 2/3 的依據：Phase 1 是否成功產出更好的 skill，以及對第二個 skill 做同樣優化時是否需要重寫大量 evaluate.py。
