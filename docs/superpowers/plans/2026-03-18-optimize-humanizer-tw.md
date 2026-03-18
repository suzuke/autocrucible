# optimize-humanizer-tw Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a crucible experiment project that iteratively optimizes the humanizer-tw skill (.md file) using LLM-as-judge evaluation.

**Architecture:** Standard crucible experiment. Agent edits `skill.md` (the humanizer-tw skill content). `evaluate.py` feeds the skill to `claude -p` as system prompt with test inputs, uses a second `claude -p` call as LLM judge, adds regex-based coverage checking and brevity scoring, and outputs a composite metric. No crucible core changes.

**Tech Stack:** Python 3, `claude` CLI (subprocess), YAML (test cases), crucible CLI (`crucible validate`, `crucible run`)

**Spec:** `docs/superpowers/specs/2026-03-18-optimize-humanizer-tw-design.md`

---

## File Structure

```
~/Documents/Hack/crucible_projects/optimize-humanizer-tw/
├── .crucible/
│   ├── config.yaml         # crucible config
│   └── program.md          # agent instructions (readonly)
├── skill.md                # humanizer-tw skill, self-contained (editable)
├── evaluate.py             # evaluation engine (hidden)
├── test_cases/
│   ├── train.yaml          # agent-visible examples (readonly)
│   └── test.yaml           # scoring test set (hidden)
├── rubric.md               # LLM judge rubric (hidden)
├── .gitignore
└── README.md
```

**Key design decision:** `skill.md` is self-contained — all content from humanizer-tw's `references/` directory (phrases.md, structures.md, examples.md) is inlined into one file. This is necessary because `evaluate.py` feeds the skill content to `claude --system-prompt` as a string, so relative file references won't resolve.

---

### Task 1: Scaffold project and git init

**Files:**
- Create: `~/Documents/Hack/crucible_projects/optimize-humanizer-tw/`
- Create: `.gitignore`
- Create: `README.md`

- [ ] **Step 1: Create project directory**

```bash
mkdir -p ~/Documents/Hack/crucible_projects/optimize-humanizer-tw/.crucible
mkdir -p ~/Documents/Hack/crucible_projects/optimize-humanizer-tw/test_cases
```

- [ ] **Step 2: Create .gitignore**

```
results-*.jsonl
run.log
results.json
__pycache__/
*.pyc
.venv/
```

- [ ] **Step 3: Create README.md**

```markdown
# optimize-humanizer-tw

Crucible experiment: optimize the humanizer-tw skill for better Chinese text de-AI-ification.

## Files
- `skill.md` — the skill being optimized (editable)
- `evaluate.py` — evaluation engine (hidden from agent)
- `test_cases/` — train (visible) and test (hidden) cases
- `rubric.md` — LLM judge scoring rubric (hidden)

## Run
crucible validate --project-dir .
crucible run --tag v1 --project-dir .
```

- [ ] **Step 4: Git init and initial commit**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-humanizer-tw
git init
git add .gitignore README.md
git commit -m "init: scaffold optimize-humanizer-tw project"
```

---

### Task 2: Prepare skill.md (inline references)

**Files:**
- Source: `~/.claude/skills/humanizer-tw/SKILL.md`
- Source: `~/.claude/skills/humanizer-tw/references/phrases.md`
- Source: `~/.claude/skills/humanizer-tw/references/structures.md`
- Source: `~/.claude/skills/humanizer-tw/references/examples.md`
- Create: `skill.md`

The original SKILL.md references three files via relative links (`[references/phrases.md]`, etc.). For the crucible experiment, we need a self-contained file because evaluate.py passes the skill content as a `--system-prompt` string.

- [ ] **Step 1: Create self-contained skill.md**

Concatenate SKILL.md + all three reference files into one `skill.md`. Replace the relative link lines (e.g., `參見 [references/phrases.md](references/phrases.md)`) with inline section headers since the content now follows directly. Keep the overall structure intact.

Structure of the merged file:
1. Original SKILL.md content (with link lines removed or replaced with "見下方詳細列表")
2. `---` separator
3. `## 附錄：高頻短語詳表` (from phrases.md)
4. `## 附錄：結構問題詳表` (from structures.md)
5. `## 附錄：改寫範例集` (from examples.md)

- [ ] **Step 2: Verify skill.md is complete**

```bash
wc -c skill.md  # record original length — needed for brevity_bonus calculation
grep -c "##" skill.md  # should have many section headers
```

Save the byte count — evaluate.py will use this as `original_len`.

- [ ] **Step 3: Commit**

```bash
git add skill.md
git commit -m "feat: add self-contained humanizer-tw skill"
```

---

### Task 3: Write test cases

**Files:**
- Create: `test_cases/train.yaml`
- Create: `test_cases/test.yaml`

Test cases must cover diverse genres and AI patterns. Each case has: id, genre, input (AI-flavored text), expected_patterns (AI patterns that should be eliminated), and tone.

- [ ] **Step 1: Write train.yaml (3 cases, agent-visible)**

These serve as examples the agent can learn from. Cover 3 different genres.

```yaml
# train.yaml — agent can see these examples
- id: train_tech_blog
  genre: 科技部落格
  input: |
    隨著雲端運算技術的快速發展，越來越多的企業開始將業務遷移到雲端。
    此外，這不僅降低了IT成本，更提高了系統的可擴展性。
    與此同時，安全性也成為企業關注的焦點。
    讓我們拭目以待，相信雲端技術將為企業帶來更多可能。
  expected_patterns:
    - 時代開場白
    - 連接詞濫用
    - 否定式排比
    - 展望類結尾
  tone: 隨意

- id: train_social_post
  genre: 社群貼文
  input: |
    眾所周知，良好的時間管理對於提升工作效率而言至關重要。
    該方法已被廣泛討論，其效果也得到了驗證。
    首先，我們需要制定計畫。其次，嚴格執行。最後，定期回顧。
    這是一個值得每個人深思的問題。
  expected_patterns:
    - 共識開場白
    - 書面代詞
    - 被動語態
    - 公式化結構
    - 反思類結尾
  tone: 輕鬆

- id: train_business
  genre: 商業文案
  input: |
    我們聚焦用戶痛點，通過深耕垂直賽道，打通上下游產業鏈。
    這一舉措不僅賦能了合作夥伴，更為整個生態圈注入了新的活力。
    讓我們攜手共進，為行業的發展貢獻力量！
  expected_patterns:
    - 互聯網黑話
    - 否定式排比
    - 展望類結尾
  tone: 隨意
```

- [ ] **Step 2: Write test.yaml (5 cases, hidden from agent)**

These are the actual scoring cases. Cover: 科技部落格, 社群貼文, 技術文件, 散文, 商業文案. Use DIFFERENT text from train.yaml — the agent must not be able to memorize answers.

```yaml
# test.yaml — hidden from agent, used for scoring
- id: test_tech_article
  genre: 科技部落格
  input: |
    在人工智慧蓬勃發展的背景下，自然語言處理技術取得了顯著突破。
    不可否認，大型語言模型的出現標誌著NLP領域進入了新的階段。
    首先，它們展現了強大的文本理解能力。其次，生成品質也有了質的飛躍。
    此外，多模態能力的加入更是錦上添花。
    我們相信，隨著技術的不斷進步，NLP將在更多領域發揮其巨大潛力。
  expected_patterns:
    - 時代開場白
    - 共識開場白
    - 動詞術語
    - 公式化結構
    - 連接詞濫用
    - 展望類結尾
  tone: 專業但易讀

- id: test_social_casual
  genre: 社群貼文
  input: |
    最近開始學習程式設計，這是一個非常有趣的事情。
    對於初學者而言，選擇合適的程式語言至關重要。
    有人認為Python最適合入門，也有人認為JavaScript更實用。
    不管怎樣，持續學習總是最重要的。希望這篇文章對您有所幫助。
  expected_patterns:
    - 翻譯腔
    - 書面語過重
    - 缺乏個人觀點
    - 絕對詞
    - 結尾套話
  tone: 輕鬆口語

- id: test_technical_doc
  genre: 技術文件
  input: |
    該系統採用微服務架構，其核心組件包括用戶服務、訂單服務和支付服務。
    在部署方面，我們予以了充分的考量，基於容器化技術進行了全方位的優化。
    此外，系統的可觀測性也得到了顯著提升，不僅實現了日誌的集中管理，
    更建立了完善的監控告警機制。綜上所述，該架構方案能夠有效滿足業務需求。
  expected_patterns:
    - 書面代詞
    - 書面語過重
    - 連接詞濫用
    - 否定式排比
    - 總結連接
  tone: 技術但自然

- id: test_prose
  genre: 散文
  input: |
    隨著季節的更迭，城市的面貌也在悄然改變。
    漫步在街頭，你會發現每一個角落都蘊含著獨特的故事。
    這不僅僅是一次簡單的散步，更是一場心靈的旅行。
    或許，生活的意義就在這些不經意的瞬間中被發現。
    讓我們珍惜當下，擁抱生活中的每一份美好。
  expected_patterns:
    - 時代開場白
    - 否定式排比
    - 反思類結尾
    - 展望類結尾
    - 動詞術語
  tone: 感性自然

- id: test_marketing
  genre: 商業文案
  input: |
    在數位轉型的浪潮中，我們推出了全新的智慧客服解決方案。
    該產品深度賦能企業客戶服務，打通了線上線下的服務閉環。
    不僅如此，我們的AI引擎還能夠觸達每一位用戶，
    為其提供沉浸式的服務體驗。毋庸置疑，
    這將為企業的客戶服務開啟新的篇章。讓我們一起見證改變！
  expected_patterns:
    - 時代開場白
    - 互聯網黑話
    - 書面代詞
    - 共識開場白
    - 展望類結尾
  tone: 專業但不浮誇
```

- [ ] **Step 3: Commit**

```bash
git add test_cases/
git commit -m "feat: add train and test cases for evaluation"
```

---

### Task 4: Write rubric.md

**Files:**
- Create: `rubric.md`

This is the LLM judge's scoring guide. It must output structured scores that evaluate.py can parse.

- [ ] **Step 1: Write rubric.md**

```markdown
# 中文改寫品質評分標準

你是一位中文寫作品質評審。你會收到一段原始的 AI 風格中文文字和一段改寫後的文字。
請根據以下 5 個維度對改寫品質打分，每個維度 1-10 分。

## 評分維度

### 1. 直接性 (directness)
文字是否直截了當？還是充滿鋪墊和繞圈？
- 10 分：開門見山，沒有廢話
- 5 分：有些不必要的鋪墊
- 1 分：充滿套話和空洞宣告

### 2. 節奏 (rhythm)
句子長度是否有變化？讀起來有韻律感嗎？
- 10 分：長短交錯，讀起來流暢
- 5 分：偶有變化但整體偏均勻
- 1 分：每句長度幾乎一樣，機械感強

### 3. 信任度 (trust)
文字是否尊重讀者的智慧？還是過度解釋？
- 10 分：簡潔明瞭，不囉嗦
- 5 分：偶有多餘解釋
- 1 分：把讀者當傻子，反覆強調

### 4. 真實性 (authenticity)
聽起來像真人寫的嗎？有沒有個性和觀點？
- 10 分：有明確的聲音和觀點，像真人在說話
- 5 分：中性但不機械
- 1 分：明顯是 AI 生成，沒有靈魂

### 5. 精煉度 (conciseness)
還有可以刪減的內容嗎？每個字都有存在的必要嗎？
- 10 分：無冗餘，每字都有用
- 5 分：有些可刪的內容
- 1 分：大量廢話和重複

## 輸出格式

你必須嚴格按照以下 JSON 格式輸出，不要加任何其他文字：

{"directness": N, "rhythm": N, "trust": N, "authenticity": N, "conciseness": N}

其中 N 為 1-10 的整數。
```

- [ ] **Step 2: Commit**

```bash
git add rubric.md
git commit -m "feat: add LLM judge rubric"
```

---

### Task 5: Write evaluate.py

**Files:**
- Create: `evaluate.py`

This is the core evaluation engine. It:
1. Validates skill.md structure (length, required sections)
2. Runs humanization via `claude -p` for each test case
3. Judges quality via `claude -p` with rubric
4. Checks AI pattern coverage with regex
5. Computes brevity bonus
6. Aggregates into final metric and writes results.json

- [ ] **Step 1: Write evaluate.py**

```python
#!/usr/bin/env python3
"""Evaluation engine for optimize-humanizer-tw crucible experiment."""
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

# --- Constants ---
SKILL_PATH = Path("skill.md")
TEST_CASES_PATH = Path("test_cases/test.yaml")
RUBRIC_PATH = Path("rubric.md")
RESULTS_PATH = Path("results.json")

# Original skill.md character count (set after Task 2, Step 2)
ORIGINAL_LEN = None  # TODO: fill in after measuring skill.md

MAX_RATIO = 1.5
REQUIRED_SECTIONS = ["個性與靈魂", "品質評分"]

# AI pattern regexes for coverage checking
AI_PATTERNS = {
    "時代開場白": r"隨著.{2,10}的(發展|興起|普及|推進)|在.{2,10}的(背景|浪潮)下|當今時代",
    "共識開場白": r"眾所周知|不言而喻|顯而易見|毋庸置疑|不可否認|毫無疑問",
    "連接詞濫用": r"此外[，,]|另外[，,]|不僅如此|除此之外|與此同時|綜上所述|總的來說|總而言之",
    "公式化結構": r"首先.{3,30}其次.{3,30}(再次|最後|然後)",
    "否定式排比": r"不僅僅?是.{2,20}[，,]更是|不是.{2,20}[，,]而是",
    "互聯網黑話": r"賦能|痛點|閉環|賽道|深耕|打通|抓手|觸達|沉浸式|全方位",
    "書面代詞": r"[^a-zA-Z]該[^死當怎]|予以|鑑於.{2,10}的情況",
    "展望類結尾": r"讓我們拭目以待|未來可期|攜手共進|並肩前行|開啟.{2,6}新篇章|一起見證",
    "反思類結尾": r"值得.{1,4}深思|或許[，,]答案就在|也許[，,]這就是.{2,10}的意義",
    "翻譯腔": r"這是一個.{2,10}的(事情|問題|消息|現象)",
    "被動語態": r"被.{1,6}(認為|討論|採納|提交|觀察|驗證)",
    "動詞術語": r"彰顯|見證了|標誌著|體現了|擁抱",
    "結尾套話": r"希望.{2,10}對您有所幫助|如有.{2,6}請多多指教|歡迎在評論區",
    "絕對詞": r"所有.{1,4}都|總是.{2,10}的|從不|每個人都|沒有人",
    "書面語過重": r"對於.{2,10}而言|基於.{2,10}的考量|進行了.{2,10}的(工作|部署|優化)",
}


def check_structure(skill_content: str) -> list[str]:
    """Validate skill.md structure. Returns list of violations."""
    violations = []

    # Length check
    ratio = len(skill_content) / ORIGINAL_LEN
    if ratio > MAX_RATIO:
        violations.append(f"length_exceeded: ratio={ratio:.2f} > {MAX_RATIO}")

    # Required sections
    for section in REQUIRED_SECTIONS:
        if section not in skill_content:
            violations.append(f"missing_section: {section}")

    # Placeholder detection
    if re.search(r"\[description\]|\[TODO\]|<written>|\.\.\.{3,}", skill_content):
        violations.append("contains_placeholder")

    return violations


def call_claude(prompt: str, system_prompt: str, timeout: int = 60) -> str:
    """Call claude CLI with -p flag. Returns response text."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--system-prompt", system_prompt,
         "--model", "haiku", "--no-session-persistence"],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr[:200]}")
    return result.stdout.strip()


def run_humanization(skill_content: str, ai_text: str) -> str:
    """Use skill to humanize AI text via claude CLI."""
    prompt = f"請改寫以下文字，去除 AI 痕跡：\n\n{ai_text}"
    return call_claude(prompt, skill_content, timeout=90)


def judge_quality(original: str, rewritten: str, rubric: str) -> dict:
    """Use LLM judge to score rewrite quality. Returns 5-dimension scores."""
    prompt = (
        f"## 原始文字\n{original}\n\n"
        f"## 改寫後文字\n{rewritten}\n\n"
        "請根據評分標準打分。只輸出 JSON，不要其他文字。"
    )
    response = call_claude(prompt, rubric, timeout=60)

    # Extract JSON from response (handle markdown code blocks)
    json_match = re.search(r"\{[^}]+\}", response)
    if not json_match:
        print(f"  WARNING: judge returned non-JSON: {response[:100]}", file=sys.stderr)
        return {"directness": 5, "rhythm": 5, "trust": 5, "authenticity": 5, "conciseness": 5}

    scores = json.loads(json_match.group())
    # Clamp to 1-10
    for key in ["directness", "rhythm", "trust", "authenticity", "conciseness"]:
        scores[key] = max(1, min(10, int(scores.get(key, 5))))
    return scores


def check_coverage(original: str, rewritten: str, expected_patterns: list[str]) -> dict:
    """Check which AI patterns were eliminated. Returns {pattern: eliminated}."""
    results = {}
    for pattern_name in expected_patterns:
        regex = AI_PATTERNS.get(pattern_name)
        if not regex:
            continue
        was_present = bool(re.search(regex, original))
        still_present = bool(re.search(regex, rewritten))
        # Pattern eliminated if it was in original and gone in rewrite
        results[pattern_name] = was_present and not still_present
    return results


def compute_brevity(current_len: int) -> float:
    """Compute brevity bonus (0-20). Shorter = better."""
    ratio = current_len / ORIGINAL_LEN
    if ratio > MAX_RATIO:
        return 0.0
    return max(0.0, 20.0 - 20.0 * (ratio - 0.5))


def main():
    # Parse --print-metric flag
    print_metric_only = "--print-metric" in sys.argv

    # Load files
    skill_content = SKILL_PATH.read_text()
    test_cases = yaml.safe_load(TEST_CASES_PATH.read_text())
    rubric = RUBRIC_PATH.read_text()

    # Step 1: Structure check
    violations = check_structure(skill_content)
    if violations:
        print(f"Structure violations: {violations}", file=sys.stderr)
        result = {"final_metric": 0.0, "violations": violations}
        RESULTS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        if print_metric_only:
            print(f"score: 0.0")
        return

    # Step 2: Run evaluation per test case
    all_quality_scores = []
    all_coverage = []
    case_details = []

    for case in test_cases:
        case_id = case["id"]
        print(f"Evaluating {case_id}...", file=sys.stderr)

        # Humanize
        try:
            rewritten = run_humanization(skill_content, case["input"])
        except Exception as e:
            print(f"  ERROR humanizing {case_id}: {e}", file=sys.stderr)
            all_quality_scores.append(5.0)  # neutral on error
            continue

        # Judge quality
        try:
            scores = judge_quality(case["input"], rewritten, rubric)
        except Exception as e:
            print(f"  ERROR judging {case_id}: {e}", file=sys.stderr)
            scores = {"directness": 5, "rhythm": 5, "trust": 5, "authenticity": 5, "conciseness": 5}

        avg_score = sum(scores.values()) / len(scores)
        all_quality_scores.append(avg_score)

        # Coverage check
        coverage = check_coverage(case["input"], rewritten, case.get("expected_patterns", []))
        if coverage:
            eliminated = sum(1 for v in coverage.values() if v)
            total = len(coverage)
            all_coverage.append(eliminated / total)

        case_details.append({
            "id": case_id,
            "scores": scores,
            "avg_score": avg_score,
            "coverage": coverage,
            "rewritten_preview": rewritten[:200],
        })

    # Step 3: Aggregate
    quality_score = sum(all_quality_scores) / len(all_quality_scores) if all_quality_scores else 5.0
    # quality_score is avg of 1-10 scores, scale to 0-50
    quality_score_scaled = (quality_score - 1) / 9 * 50

    coverage_score = (sum(all_coverage) / len(all_coverage) * 20) if all_coverage else 0.0

    brevity_bonus = compute_brevity(len(skill_content))

    # Structure penalty (already passed check, but penalize borderline)
    structure_penalty = 0.0

    raw = quality_score_scaled + coverage_score + brevity_bonus + structure_penalty
    final_metric = max(0.0, round(raw, 2))

    # Write results
    result = {
        "final_metric": final_metric,
        "quality_score": round(quality_score_scaled, 2),
        "coverage_score": round(coverage_score, 2),
        "brevity_bonus": round(brevity_bonus, 2),
        "structure_penalty": structure_penalty,
        "skill_len": len(skill_content),
        "skill_ratio": round(len(skill_content) / ORIGINAL_LEN, 2),
        "cases": case_details,
    }
    RESULTS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    if print_metric_only:
        print(f"score: {final_metric}")
    else:
        print(f"score: {final_metric}")
        print(f"  quality: {quality_score_scaled:.1f}/50", file=sys.stderr)
        print(f"  coverage: {coverage_score:.1f}/20", file=sys.stderr)
        print(f"  brevity: {brevity_bonus:.1f}/20", file=sys.stderr)
        print(f"  penalty: {structure_penalty}/0", file=sys.stderr)


if __name__ == "__main__":
    main()
```

**IMPORTANT:** After Task 2 Step 2, fill in `ORIGINAL_LEN` with the actual character count of the initial `skill.md`.

- [ ] **Step 2: Install PyYAML dependency**

```bash
cd ~/Documents/Hack/crucible_projects/optimize-humanizer-tw
pip install pyyaml  # or ensure it's available in the environment
```

- [ ] **Step 3: Verify evaluate.py runs (dry run)**

```bash
python3 evaluate.py
```

Expected: It should attempt to call `claude` CLI. If test.yaml and skill.md exist, it will run the full pipeline. Check that `results.json` is created with the correct structure.

- [ ] **Step 4: Commit**

```bash
git add evaluate.py
git commit -m "feat: add evaluation engine"
```

---

### Task 6: Write config.yaml and program.md

**Files:**
- Create: `.crucible/config.yaml`
- Create: `.crucible/program.md`

- [ ] **Step 1: Write config.yaml**

```yaml
name: optimize-humanizer-tw
description: 優化中文去 AI 味 skill 的改寫品質

files:
  editable:
    - skill.md
  readonly:
    - test_cases/train.yaml
  hidden:
    - evaluate.py
    - test_cases/test.yaml
    - rubric.md

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

- [ ] **Step 2: Write program.md**

```markdown
# 任務：優化 humanizer-tw Skill

你的目標是改進 `skill.md`，使其指導 LLM 產出更自然、更有人味的中文改寫。

## skill.md 是什麼

這是一個 Claude Code skill 文件，當使用者需要「去 AI 味」時，Claude 會讀取這個 skill 作為指引來改寫文字。你要優化這個指引的品質。

## 你可以做的

- 改善規則描述的清晰度和優先順序
- 增加或修改改寫前/後範例
- 調整段落結構和分類方式
- 新增 skill 未覆蓋的 AI 模式規則
- 精簡冗餘的描述（更短的 skill 會得到更高分）
- 改善「個性與靈魂」段落的指導效果

## 你不能做的

- 刪除「個性與靈魂」段落
- 刪除「品質評分」段落
- 讓 skill.md 超過原始長度的 1.5 倍
- 加入條件邏輯（如「如果看到特定文字就輸出特定回應」）
- 加入針對特定測試文本的硬編碼回應

## 評分維度

你的修改會根據以下維度評分：

1. **品質 (50分)** — 改寫輸出的 5 維度評分：直接性、節奏、信任度、真實性、精煉度
2. **覆蓋率 (20分)** — 改寫是否成功消除 AI 模式（時代開場白、連接詞、互聯網黑話等）
3. **簡潔度 (20分)** — skill.md 越精簡越好。同等品質下，更短的 skill 得更高分

## 策略建議

- 先 READ skill.md 了解目前的結構和內容
- 參考 test_cases/train.yaml 了解測試文本的風格
- 優先優化「投入產出比」最高的改動：哪些規則描述最冗長但效果最差？
- 範例很重要 — 好的改寫前/後對照比長篇規則描述更有效
- 「個性與靈魂」段落是改寫品質的核心 — 加強它比加規則更有效

## 工作流程

1. READ skill.md — 理解目前結構
2. READ test_cases/train.yaml — 了解測試風格
3. THINK — 分析哪些地方可以改進
4. EDIT — 做有針對性的修改
5. EXPLAIN — 說明你改了什麼、為什麼
```

- [ ] **Step 3: Commit**

```bash
git add .crucible/
git commit -m "feat: add crucible config and program"
```

---

### Task 7: Validate and seed baseline

**Files:**
- Modify: `evaluate.py` (fill in ORIGINAL_LEN)

- [ ] **Step 1: Measure original skill.md length and update evaluate.py**

```bash
python3 -c "print(len(open('skill.md').read()))"
```

Update the `ORIGINAL_LEN` constant in `evaluate.py` with this value.

- [ ] **Step 2: Run crucible validate**

```bash
crucible validate --project-dir .
```

Expected: All checks pass (config valid, files exist, command runs, metric parsed).

If validation fails, fix issues and re-run.

- [ ] **Step 3: Run baseline evaluation**

```bash
python3 -u evaluate.py
cat results.json | python3 -m json.tool
```

Record the baseline score. This is the score with the unmodified humanizer-tw skill. It should be in the 40-60 range.

- [ ] **Step 4: Commit final state**

```bash
git add -A
git commit -m "feat: complete project setup, baseline ready"
```

- [ ] **Step 5: Run the experiment**

```bash
crucible run --tag v1 --project-dir .
```

Monitor output. Expected: 15-20 iterations, each ~2-3 minutes, with gradual metric improvement.

---

## Notes

### Cost Considerations
- Each iteration: ~10 LLM calls (5 test cases × 2 calls each) + 1 agent call
- Using `--model haiku` for humanization and judging keeps costs low
- Agent (crucible's optimizer) uses the default model
- Estimated total: ~200 LLM calls for a full 20-iteration run

### After the Run
- `crucible history --tag v1` to see all iterations
- `git diff main..crucible/v1 -- skill.md` to see total changes
- Copy the optimized `skill.md` back to `~/.claude/skills/humanizer-tw/SKILL.md` (remember to re-split references if needed)
- The optimized skill may work better as a self-contained file (no references directory)

### Troubleshooting
- If `claude -p` fails: check that `claude` CLI is in PATH and authenticated
- If timeout: increase `constraints.timeout_seconds` (5 LLM calls can be slow)
- If metric is always 0: check `ORIGINAL_LEN` is set correctly, check structure violations in results.json
- If scores are all ~5: check that rubric.md is being read correctly, LLM judge may not be following format
