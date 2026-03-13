# New Examples Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add 9 new example experiments to `src/crucible/examples/` showcasing Crucible's versatility.

**Architecture:** Each example follows the standard Crucible layout: editable file(s) the agent modifies, a hidden `evaluate.py` harness that outputs `metric: value`, and `.crucible/config.yaml` + `.crucible/program.md`. All examples are self-contained (stdlib or numpy only, except prompt examples which call `claude` CLI subprocess).

**Tech Stack:** Python 3.10+, stdlib, numpy (for quantize only), `claude` CLI (for prompt examples)

**Implementation Order:** fast examples first (tokenizer→regex→hash), then prompt series, then ML/meta.

---

## Task 1: optimize-tokenizer

**Files:**
- Create: `src/crucible/examples/optimize-tokenizer/tokenizer.py`
- Create: `src/crucible/examples/optimize-tokenizer/corpus.txt`
- Create: `src/crucible/examples/optimize-tokenizer/evaluate.py`
- Create: `src/crucible/examples/optimize-tokenizer/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-tokenizer/.crucible/program.md`
- Create: `src/crucible/examples/optimize-tokenizer/.gitignore`

**Step 1: Create directory**

```bash
mkdir -p src/crucible/examples/optimize-tokenizer/.crucible
```

**Step 2: Write corpus.txt (training data, visible to agent)**

~3KB of Wikipedia-style text. The agent uses this for training. Evaluation uses different (news) text embedded in evaluate.py.

```
src/crucible/examples/optimize-tokenizer/corpus.txt
```

Content: A few paragraphs of encyclopedia-style English (science, history, geography). At least 3000 chars. Example:

```
The Python programming language was created by Guido van Rossum and first released in 1991. Python emphasizes code readability and simplicity, using significant whitespace to delimit code blocks rather than curly braces or keywords. The language supports multiple programming paradigms, including procedural, object-oriented, and functional programming.

Machine learning is a subset of artificial intelligence that enables systems to learn and improve from experience without being explicitly programmed. The process begins with observations or data, such as examples, direct experience, or instruction, so that computers can look for patterns in data and make better decisions in the future.

The Roman Empire was one of the largest empires in ancient history. At its greatest extent, it covered much of Europe, North Africa, and Western Asia. The empire was characterized by a highly centralized government, a professional army, and an extensive network of roads that facilitated trade and military movement.

Photosynthesis is the process by which plants, algae, and some bacteria convert light energy into chemical energy stored in glucose. This process occurs primarily in the chloroplasts of plant cells, which contain the green pigment chlorophyll. The overall equation for photosynthesis combines carbon dioxide and water using light energy to produce glucose and oxygen.

The Internet is a global network of interconnected computers that communicate using standardized protocols. Originally developed by the United States Department of Defense in the 1960s as ARPANET, the internet has evolved into a vast infrastructure supporting email, the World Wide Web, streaming media, and countless other services used by billions of people worldwide.
```

**Step 3: Write tokenizer.py (starter — character-level, no merges)**

```python
"""Tokenizer implementation — edit this file to improve compression.

Interface:
  build_merges(corpus: str) -> list[tuple[str, str]]
      Analyze corpus and return up to 500 BPE merge rules.
      Each rule is a pair of adjacent token strings to merge, e.g. ("t", "h") -> "th".

  tokenize(text: str, merges: list[tuple[str, str]]) -> list[str]
      Apply merge rules to text and return list of tokens.

Baseline: no merges → every character is its own token → tokens_per_char = 1.0
Goal: find merge rules that reduce tokens_per_char on held-out text.
"""


def build_merges(corpus: str) -> list[tuple[str, str]]:
    """Return merge rules derived from corpus. Max 500 rules."""
    # Baseline: no merges (character-level tokenization)
    return []


def tokenize(text: str, merges: list[tuple[str, str]]) -> list[str]:
    """Apply merge rules to tokenize text into subword tokens."""
    # Start: split into individual characters
    tokens = list(text)

    for a, b in merges:
        merged = a + b
        new_tokens = []
        i = 0
        while i < len(tokens):
            if i < len(tokens) - 1 and tokens[i] == a and tokens[i + 1] == b:
                new_tokens.append(merged)
                i += 2
            else:
                new_tokens.append(tokens[i])
                i += 1
        tokens = new_tokens

    return tokens
```

**Step 4: Write evaluate.py (hidden harness)**

```python
"""Evaluation harness for tokenizer.py — DO NOT MODIFY.

Measures average tokens per character on a held-out news corpus.
Lower tokens_per_char = better compression.

Output format (parsed by crucible):
    tokens_per_char: <float>
    num_merges: <int>
    num_tokens: <int>
"""

import sys
import traceback

# Held-out test corpus: news style, different from training corpus.txt
# Agent trains on Wikipedia-style text; we test on news-style text.
TEST_CORPUS = """\
Scientists announced today the discovery of a new species of deep-sea fish \
near the Mariana Trench. The creature, which glows blue in complete darkness, \
was captured on video for the first time by a remotely operated vehicle. \
Researchers from the National Oceanic and Atmospheric Administration said \
the fish exhibits unusual feeding behavior not previously observed in related species.

The city council approved a new budget plan that allocates funding for road \
repairs and public transportation upgrades. The measure passed with a seven to \
two vote after months of debate over how to balance infrastructure spending with \
education priorities. Officials estimate the improvements will benefit approximately \
two hundred thousand daily commuters across the metropolitan area.

Global temperatures continued to break records this year according to data \
released by climate scientists. The report shows that average surface temperatures \
have risen by nearly one and a half degrees Celsius compared to pre-industrial \
levels. Experts warn that without significant reductions in greenhouse gas emissions, \
the frequency of extreme weather events will continue to increase throughout this decade.

Shares in technology companies fell sharply after the central bank announced \
an unexpected interest rate increase. The benchmark index dropped three percent \
in early trading before recovering some losses by afternoon. Analysts suggest \
investors are reassessing growth projections for software and semiconductor firms \
in light of higher borrowing costs and tightening credit conditions.

Health authorities confirmed the effectiveness of a new vaccine against a \
respiratory illness that affected millions last winter. Clinical trials involving \
forty thousand participants showed ninety two percent protection against severe disease. \
The vaccine is expected to receive full regulatory approval within the next six weeks \
and will be distributed through existing pharmacy networks.\
"""


def main():
    try:
        from tokenizer import build_merges, tokenize

        with open("corpus.txt", "r") as f:
            train_corpus = f.read()

        # Build merge rules from training corpus
        merges = build_merges(train_corpus)

        if not isinstance(merges, list):
            print("ERROR: build_merges must return a list")
            print("tokens_per_char: 999.0")
            sys.exit(0)

        if len(merges) > 500:
            print(f"VIOLATION: too many merge rules ({len(merges)} > 500), truncating to 500")
            merges = merges[:500]

        # Validate merge rule format
        for i, rule in enumerate(merges):
            if not (isinstance(rule, tuple) and len(rule) == 2 and
                    isinstance(rule[0], str) and isinstance(rule[1], str)):
                print(f"ERROR: merge rule {i} must be tuple[str, str], got {type(rule)}")
                print("tokens_per_char: 999.0")
                sys.exit(0)

        # Tokenize the test corpus
        tokens = tokenize(TEST_CORPUS, merges)

        if not isinstance(tokens, list):
            print("ERROR: tokenize must return a list")
            print("tokens_per_char: 999.0")
            sys.exit(0)

        # Verify round-trip: joining tokens must reconstruct original text
        reconstructed = "".join(tokens)
        if reconstructed != TEST_CORPUS:
            print("ERROR: tokenize is lossy — joining tokens does not reconstruct original text")
            print(f"  expected length: {len(TEST_CORPUS)}, got: {len(reconstructed)}")
            print("tokens_per_char: 999.0")
            sys.exit(0)

        tokens_per_char = len(tokens) / len(TEST_CORPUS)
        print(f"tokens_per_char: {tokens_per_char:.4f}")
        print(f"num_merges: {len(merges)}")
        print(f"num_tokens: {len(tokens)}")
        print(f"num_chars: {len(TEST_CORPUS)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("tokens_per_char: 999.0")


if __name__ == "__main__":
    main()
```

**Step 5: Write config.yaml**

```yaml
name: "optimize-tokenizer"

files:
  editable:
    - "tokenizer.py"
  readonly:
    - "corpus.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "tokens_per_char"
  direction: "minimize"

constraints:
  timeout_seconds: 30
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 6: Write program.md**

```markdown
# Tokenizer Optimization

You are optimizing a BPE-style tokenizer to compress English text.

## Goal

Minimize `tokens_per_char` — average tokens per character on a held-out test corpus.
Lower = better compression. Baseline (no merges) = 1.0 tokens/char.

## How It Works

1. `build_merges(corpus)` — analyzes `corpus.txt` and returns merge rules
2. `tokenize(text, merges)` — applies rules to tokenize text into subword tokens
3. Evaluation runs your tokenizer on a **different** English corpus (not corpus.txt)

## Rules

- Edit only `tokenizer.py`
- Return at most 500 merge rules from `build_merges()`
- Each merge rule is a `tuple[str, str]`, e.g. `("t", "h")` merges adjacent "t"+"h" → "th"
- `tokenize()` must be lossless: `"".join(tokenize(text, merges)) == text`
- Standard library only (no external packages)

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `tokenizer.py`

## Strategy

Read `corpus.txt` to understand the text style. Common BPE approach:
1. Count all adjacent character pairs in corpus
2. Merge the most frequent pair
3. Repeat until you have 500 rules

The more frequent an adjacent pair, the more tokens you save by merging it.
```

**Step 7: Write .gitignore**

```
run.log
__pycache__/
*.pyc
```

**Step 8: Smoke test**

```bash
cd src/crucible/examples/optimize-tokenizer && python3 evaluate.py
```

Expected output:
```
tokens_per_char: 1.0000
num_merges: 0
num_tokens: <large number>
```

**Step 9: Commit**

```bash
git add src/crucible/examples/optimize-tokenizer/
git commit -m "feat: add optimize-tokenizer example"
```

---

## Task 2: optimize-regex

**Files:**
- Create: `src/crucible/examples/optimize-regex/pattern.py`
- Create: `src/crucible/examples/optimize-regex/examples.txt`
- Create: `src/crucible/examples/optimize-regex/evaluate.py`
- Create: `src/crucible/examples/optimize-regex/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-regex/.crucible/program.md`
- Create: `src/crucible/examples/optimize-regex/.gitignore`

**Step 1: Create directory**

```bash
mkdir -p src/crucible/examples/optimize-regex/.crucible
```

**Step 2: Write examples.txt (20 valid + 20 invalid, visible to agent)**

```
# Email validation examples
# Format: label<TAB>email
# Labels: VALID or INVALID

VALID	user@example.com
VALID	alice.bob@domain.org
VALID	test+tag@sub.example.co.uk
VALID	user123@company.net
VALID	first.last@university.edu
VALID	me@x.io
VALID	support@help-desk.com
VALID	no-reply@news.example.com
VALID	admin@192.168.0.1
VALID	user_name@domain.info
VALID	a@b.co
VALID	very.unusual."@".unusual.com@example.com
VALID	user@domain-name.com
VALID	x@domain.museum
VALID	user@domain.travel
VALID	hello@world.dev
VALID	test@test.test
VALID	u@domain.com
VALID	name+filter@gmail.com
VALID	123@numbers.com

INVALID	plainaddress
INVALID	@missinglocal.com
INVALID	user@
INVALID	user@.com
INVALID	user@domain..com
INVALID	user name@domain.com
INVALID	user@@domain.com
INVALID	user@domain
INVALID	.user@domain.com
INVALID	user.@domain.com
INVALID	user@-domain.com
INVALID	user@domain-.com
INVALID	user@domain.c
INVALID	user@domain.toolongextension
INVALID	()user@domain.com
INVALID	user@domain@domain.com
INVALID	user@[domain].com
INVALID	user@dom ain.com
INVALID	@
INVALID	user@.
```

**Step 3: Write pattern.py (starter)**

```python
"""Email pattern — edit PATTERN to improve F1 score on the held-out test set.

PATTERN is a single Python regex string.
It will be tested with re.fullmatch(PATTERN, email_address).

Baseline: simple pattern that gets ~0.70 F1.
A well-crafted pattern can reach ~0.95+ F1.
"""

# Baseline: catches most emails but misses edge cases
PATTERN = r"\S+@\S+\.\S+"
```

**Step 4: Write evaluate.py (hidden)**

```python
"""Evaluation harness for pattern.py — DO NOT MODIFY.

Tests the regex pattern against 200 labeled email addresses.
Computes precision, recall, and F1 score.

Output format (parsed by crucible):
    f1_score: <float>
    precision: <float>
    recall: <float>
    tp: <int>
    fp: <int>
    fn: <int>
"""

import re
import sys
import time
import traceback

# 200 labeled email addresses (held-out, not shown to agent)
# Format: (email_string, is_valid: bool)
SAMPLES = [
    # Valid emails
    ("user@example.com", True),
    ("alice.bob@domain.org", True),
    ("test+tag@sub.example.co.uk", True),
    ("user123@company.net", True),
    ("first.last@university.edu", True),
    ("me@x.io", True),
    ("support@help-desk.com", True),
    ("no-reply@news.example.com", True),
    ("admin@192.168.0.1", True),
    ("user_name@domain.info", True),
    ("a@b.co", True),
    ("user@domain-name.com", True),
    ("hello@world.dev", True),
    ("test@test.test", True),
    ("u@domain.com", True),
    ("name+filter@gmail.com", True),
    ("123@numbers.com", True),
    ("contact@shop.store", True),
    ("info@company.io", True),
    ("dev@api.example.com", True),
    ("user.middle.last@domain.com", True),
    ("x+y+z@domain.org", True),
    ("test-1@domain-2.com", True),
    ("abc@xyz.museum", True),
    ("a1b2c3@d4e5.net", True),
    ("my.email@sub.domain.co.uk", True),
    ("user@domain.travel", True),
    ("noreply@auto.mailer.com", True),
    ("support+ticket-123@help.io", True),
    ("admin@10.0.0.1", True),
    ("user@domain.name", True),
    ("hello+world@foo.bar", True),
    ("simple@example.co", True),
    ("with_underscore@domain.com", True),
    ("hyphen-ok@domain.com", True),
    ("numbers123@domain456.com", True),
    ("dot.in.local@domain.com", True),
    ("plus+in+local@domain.com", True),
    ("mix_of-chars@domain.org", True),
    ("two@char.cc", True),
    ("user@sub1.sub2.example.com", True),
    ("a@a.aa", True),
    ("test@domain.solutions", True),
    ("user@company.global", True),
    ("x@y.z", True),
    ("email@123.123.123.123", True),
    ("1234567890@domain.com", True),
    ("email@domain.co.jp", True),
    ("email@subdomain.domain.com", True),
    ("firstname+lastname@domain.com", True),
    # Invalid emails
    ("plainaddress", False),
    ("@missinglocal.com", False),
    ("user@", False),
    ("user@.com", False),
    ("user@domain..com", False),
    ("user name@domain.com", False),
    ("user@@domain.com", False),
    ("user@domain", False),
    (".user@domain.com", False),
    ("user.@domain.com", False),
    ("user@-domain.com", False),
    ("user@domain-.com", False),
    ("user@domain.c", False),
    ("()user@domain.com", False),
    ("user@domain@domain.com", False),
    ("user@dom ain.com", False),
    ("@", False),
    ("user@.", False),
    ("", False),
    ("just.a.string", False),
    ("missing@tld.", False),
    ("double..dot@domain.com", False),
    ("user @domain.com", False),
    (" user@domain.com", False),
    ("user@domain.com ", False),
    ("user@[invalid].com", False),
    ("user#tag@domain.com", False),
    ("user!name@domain.com", False),
    ("user$name@domain.com", False),
    ("user%name@domain.com", False),
    ("user^name@domain.com", False),
    ("user&name@domain.com", False),
    ("user*name@domain.com", False),
    ("user(name@domain.com", False),
    ("user)name@domain.com", False),
    ("user=name@domain.com", False),
    ("[user]@domain.com", False),
    ("{user}@domain.com", False),
    ("user|name@domain.com", False),
    ("user\\name@domain.com", False),
    ("user;name@domain.com", False),
    ("user:name@domain.com", False),
    ("user'name@domain.com", False),
    ('user"name@domain.com', False),
    ("user<name@domain.com", False),
    ("user>name@domain.com", False),
    ("user,name@domain.com", False),
    ("user?name@domain.com", False),
    ("user/name@domain.com", False),
    ("@domain.com", False),
    ("user@", False),
    ("nodomain@", False),
    # More valid
    ("valid.email+suffix@domain.org", True),
    ("firstname.lastname@domain.com", True),
    ("email@domain.com", True),
    ("email@domain.info", True),
    ("email@domain.name", True),
    ("email@domain.mobi", True),
    ("email@domain.pro", True),
    ("email@domain.aero", True),
    ("email@domain.coop", True),
    ("email@domain.museum", True),
    ("test.email.with+symbol@domain.com", True),
    ("id-with-dash@domain.com", True),
    ("example-indeed@strange-domain.com", True),
    ("example.firstname.lastname@domain.com", True),
    ("try-this@domain.co.uk", True),
    ("send-here@domain.org.au", True),
    ("a+b@c.d", True),
    ("info+alerts@newsite.example.com", True),
    ("user.99@domain.com", True),
    ("user-00@domain-99.com", True),
    # More invalid
    ("missingatsign.domain.com", False),
    ("missing@dot", False),
    ("two@@at.com", False),
    ("..double@domain.com", False),
    ("domain@.start.dot.com", False),
    ("domain@end.dot.com.", False),
    ("space in@domain.com", False),
    ("space@in domain.com", False),
    ("tab\tin@domain.com", False),
    ("newline\n@domain.com", False),
    ("@nodomain", False),
    ("no@tld", False),
    ("a@b.c1", False),
    ("a@b.cc3", False),
    ("a@b.1com", False),
    ("a@b.com1", False),
    ("local@-hyphen.com", False),
    ("local@hyphen-.com", False),
    ("local@.leading.com", False),
    ("local@trailing.com.", False),
    ("a@b", False),
    ("a@b.", False),
    (".@domain.com", False),
    ("a..b@domain.com", False),
    ("a@b..c", False),
]


def check_catchall(pattern_str: str) -> bool:
    """Returns True if pattern is an obvious catch-all."""
    stripped = pattern_str.strip()
    catchalls = [".*", ".+", r"\S+", r"\w+"]
    return stripped in catchalls


def main():
    try:
        with open("pattern.py", "r") as f:
            source = f.read()

        # Import pattern
        import importlib.util
        spec = importlib.util.spec_from_file_location("pattern", "pattern.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pattern_str = mod.PATTERN

        if check_catchall(pattern_str):
            print("VIOLATION: catch-all pattern not allowed")
            print("f1_score: 0.0")
            return

        compiled = re.compile(pattern_str)

        # Time the matching (must complete in < 2s total)
        t0 = time.perf_counter()
        results = []
        for text, label in SAMPLES:
            matched = bool(compiled.fullmatch(text))
            results.append((matched, label))
        elapsed = time.perf_counter() - t0

        if elapsed > 2.0:
            print(f"TIMEOUT: pattern matching took {elapsed:.2f}s > 2s limit")
            print("f1_score: 0.0")
            return

        tp = sum(1 for m, l in results if m and l)
        fp = sum(1 for m, l in results if m and not l)
        fn = sum(1 for m, l in results if not m and l)
        tn = sum(1 for m, l in results if not m and not l)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) > 0 else 0.0)

        print(f"f1_score: {f1:.4f}")
        print(f"precision: {precision:.4f}")
        print(f"recall: {recall:.4f}")
        print(f"tp: {tp}")
        print(f"fp: {fp}")
        print(f"fn: {fn}")
        print(f"tn: {tn}")
        print(f"match_time_ms: {elapsed * 1000:.2f}")

    except re.error as e:
        print(f"ERROR: invalid regex: {e}")
        print("f1_score: 0.0")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("f1_score: 0.0")


if __name__ == "__main__":
    main()
```

**Step 5: Write config.yaml**

```yaml
name: "optimize-regex"

files:
  editable:
    - "pattern.py"
  readonly:
    - "examples.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "f1_score"
  direction: "maximize"

constraints:
  timeout_seconds: 30
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 6: Write program.md**

```markdown
# Regex Optimization

You are designing a regex pattern to classify email addresses.

## Goal

Maximize `f1_score` on a held-out set of 200 labeled email addresses.
F1 = harmonic mean of precision and recall.

## Setup

- `pattern.py` contains `PATTERN: str` — a single Python regex string
- The evaluator runs `re.fullmatch(PATTERN, email)` on each test address
- VALID emails → should match; INVALID emails → should not match

## Rules

- Edit only `pattern.py`
- `PATTERN` must be a valid Python regex string
- Pattern must process all test samples in under 2 seconds total
- Catch-all patterns like `.*` or `\S+` alone are rejected
- Standard library `re` module only

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `pattern.py`

## Reference

See `examples.txt` for 20 valid and 20 invalid examples (different from the test set).

Common email structure: `local-part @ domain . tld`
- Local part: letters, digits, `.`, `+`, `-`, `_` (no spaces, not starting/ending with `.`)
- Domain: labels separated by `.`, each label: letters/digits/hyphens (no leading/trailing hyphen)
- TLD: 2+ letters only
```

**Step 7: Smoke test**

```bash
cd src/crucible/examples/optimize-regex && python3 evaluate.py
```

Expected: f1_score around 0.65–0.75 for the baseline `\S+@\S+\.\S+`.

**Step 8: Commit**

```bash
git add src/crucible/examples/optimize-regex/
git commit -m "feat: add optimize-regex example"
```

---

## Task 3: optimize-hash

**Files:**
- Create: `src/crucible/examples/optimize-hash/hasher.py`
- Create: `src/crucible/examples/optimize-hash/key_sample.txt`
- Create: `src/crucible/examples/optimize-hash/evaluate.py`
- Create: `src/crucible/examples/optimize-hash/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-hash/.crucible/program.md`
- Create: `src/crucible/examples/optimize-hash/.gitignore`

**Step 1: Create directory**

```bash
mkdir -p src/crucible/examples/optimize-hash/.crucible
```

**Step 2: Write key_sample.txt (100 sample keys)**

Generate with Python and paste output. Run this once to generate:

```python
import random
rng = random.Random(42)
keys = []
words = ["the","of","and","a","to","in","is","you","that","it","he","was",
         "for","on","are","as","with","his","they","at","be","this","have",
         "from","or","one","had","by","but","not","what","all","were","we",
         "when","your","can","said","there","use","an","each","which","she",
         "do","how","their","if","will","up","other","about","out","many"]
for i in range(40):
    keys.append(rng.choice(words) + str(rng.randint(1, 9999)))
for _ in range(30):
    keys.append(f"{rng.randint(0,0xffffffff):08x}-{rng.randint(0,0xffff):04x}-{rng.randint(0,0xffff):04x}")
for i in range(30):
    keys.append(str(i * rng.randint(100, 9999)))
for k in keys:
    print(k)
```

The key_sample.txt file should have 100 lines (one key per line) from this script.

**Step 3: Write hasher.py (starter)**

```python
"""Hash function — edit hash_fn to improve uniformity.

Interface:
  hash_fn(key: str, table_size: int) -> int
      Returns an integer in range [0, table_size).

Baseline: polynomial hash with small prime — gets moderate uniformity.
Goal: design a hash that distributes 50,000 keys uniformly across table_size buckets.

Constraints:
  - Cannot use Python's built-in hash() function
  - Cannot use hashlib
  - Pure Python arithmetic only
"""


def hash_fn(key: str, table_size: int) -> int:
    """Hash a string key to [0, table_size). Baseline: polynomial rolling hash."""
    h = 0
    for ch in key:
        h = h * 31 + ord(ch)
    return h % table_size
```

**Step 4: Write evaluate.py (hidden)**

```python
"""Evaluation harness for hasher.py — DO NOT MODIFY.

Hashes 50,000 keys into a table of size 65,537 (prime).
Measures uniformity using chi-square statistic.
Higher uniformity_score = more uniform distribution = better hash.

Output format (parsed by crucible):
    uniformity_score: <float>   (0.0 to 1.0, higher is better)
    collision_rate: <float>
    chi_square: <float>
"""

import ast
import math
import random
import sys
import traceback

TABLE_SIZE = 65537  # prime
NUM_KEYS = 50_000
SEED = 12345


def check_forbidden(source: str) -> str | None:
    """Returns error message if forbidden functions are used."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"SyntaxError: {e}"

    for node in ast.walk(tree):
        # Check for hash() builtin call
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "hash":
                return "builtin hash() is forbidden — implement your own hash function"
        # Check for hashlib import
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "hashlib":
                    return "hashlib is forbidden — implement your own hash function"
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "hashlib":
                return "hashlib is forbidden"
    return None


def generate_keys(rng: random.Random) -> list[str]:
    """Generate 50k diverse string keys."""
    keys = []
    words = [
        "the", "of", "and", "a", "to", "in", "is", "you", "that", "it",
        "he", "was", "for", "on", "are", "as", "with", "his", "they", "at",
        "be", "this", "have", "from", "or", "one", "had", "by", "but", "not",
        "what", "all", "were", "we", "when", "your", "can", "said", "there",
        "use", "an", "each", "which", "she", "do", "how", "their", "if", "will",
    ]
    # English word + number combos
    for _ in range(20_000):
        keys.append(rng.choice(words) + str(rng.randint(1, 999_999)))
    # UUID-style hex strings
    for _ in range(15_000):
        keys.append(
            f"{rng.randint(0, 0xffffffff):08x}-"
            f"{rng.randint(0, 0xffff):04x}-"
            f"{rng.randint(0, 0xffff):04x}"
        )
    # Numeric strings
    for i in range(15_000):
        keys.append(str(rng.randint(0, 10_000_000)))
    return keys


def main():
    with open("hasher.py", "r") as f:
        source = f.read()

    violation = check_forbidden(source)
    if violation:
        print(f"VIOLATION: {violation}")
        print("uniformity_score: 0.0")
        return

    try:
        from hasher import hash_fn

        rng = random.Random(SEED)
        keys = generate_keys(rng)

        buckets = [0] * TABLE_SIZE
        for key in keys:
            idx = hash_fn(key, TABLE_SIZE)
            if not isinstance(idx, int):
                print(f"ERROR: hash_fn must return int, got {type(idx)}")
                print("uniformity_score: 0.0")
                return
            buckets[idx % TABLE_SIZE] += 1

        # Collision rate: fraction of keys that landed in an already-occupied bucket
        collisions = sum(max(0, b - 1) for b in buckets)
        collision_rate = collisions / NUM_KEYS

        # Chi-square test for uniformity
        expected = NUM_KEYS / TABLE_SIZE
        chi_sq = sum((b - expected) ** 2 / expected for b in buckets)

        # Normalize uniformity score: perfect uniform → chi_sq ≈ TABLE_SIZE-1 (expected)
        # A random hash → chi_sq ≈ TABLE_SIZE (Poisson)
        # Bad hash → chi_sq >> TABLE_SIZE
        # Score = 1 / (1 + excess_chi / TABLE_SIZE)
        expected_chi = TABLE_SIZE - 1  # theoretical for uniform
        excess = max(0.0, chi_sq - expected_chi)
        uniformity_score = 1.0 / (1.0 + excess / TABLE_SIZE)

        print(f"uniformity_score: {uniformity_score:.4f}")
        print(f"collision_rate: {collision_rate:.4f}")
        print(f"chi_square: {chi_sq:.2f}")
        print(f"expected_chi: {expected_chi:.2f}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("uniformity_score: 0.0")


if __name__ == "__main__":
    main()
```

**Step 5: Write config.yaml**

```yaml
name: "optimize-hash"

files:
  editable:
    - "hasher.py"
  readonly:
    - "key_sample.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "uniformity_score"
  direction: "maximize"

constraints:
  timeout_seconds: 30
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 6: Write program.md**

```markdown
# Hash Function Optimization

You are designing a hash function that distributes string keys uniformly.

## Goal

Maximize `uniformity_score` — how evenly 50,000 keys are distributed across 65,537 buckets.
Score ranges from 0 (terrible) to 1 (perfect uniform distribution).

## Interface

```python
def hash_fn(key: str, table_size: int) -> int:
    """Return an integer. Will be taken mod table_size internally."""
```

## Rules

- Edit only `hasher.py`
- Cannot use Python's built-in `hash()` function (AST-checked)
- Cannot use `hashlib` (AST-checked)
- Pure Python arithmetic only (`ord()`, `abs()`, bit operations are fine)
- Standard library math/random are allowed

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `hasher.py`

## Key Distribution

See `key_sample.txt` for 100 example keys. The full test set includes:
- English word + number combinations (e.g. "apple42")
- UUID-style hex strings (e.g. "3f2a1b0c-dead-beef")
- Numeric strings (e.g. "7391842")

## Strategy

Good hash functions mix bits thoroughly so that similar keys map to different buckets:
- Polynomial rolling hash: `h = h * PRIME + ord(ch)`
- FNV-1a: XOR then multiply by a large prime
- Bit mixing: shifts, XORs, multiplications
The choice of prime and mixing constants matters significantly.
```

**Step 7: Smoke test**

```bash
cd src/crucible/examples/optimize-hash && python3 evaluate.py
```

Expected: uniformity_score around 0.5–0.8 for the baseline polynomial hash.

**Step 8: Commit**

```bash
git add src/crucible/examples/optimize-hash/
git commit -m "feat: add optimize-hash example"
```

---

## Task 4: optimize-prompt-math

**Files:**
- Create: `src/crucible/examples/optimize-prompt-math/prompt.txt`
- Create: `src/crucible/examples/optimize-prompt-math/examples.txt`
- Create: `src/crucible/examples/optimize-prompt-math/evaluate.py`
- Create: `src/crucible/examples/optimize-prompt-math/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-prompt-math/.crucible/program.md`
- Create: `src/crucible/examples/optimize-prompt-math/.gitignore`

**Step 1: Verify claude CLI is available**

```bash
which claude && claude --version
```

Must succeed. The evaluator calls `claude` as a subprocess.

**Step 2: Create directory**

```bash
mkdir -p src/crucible/examples/optimize-prompt-math/.crucible
```

**Step 3: Write examples.txt (3 sample math problems — different from test set)**

```
# Sample math word problems (these are EXAMPLES ONLY — not the test set)
# Your prompt will be tested on 10 different problems.

Example 1:
  Question: A store sells apples for $0.50 each. Maria buys 8 apples and pays with a $10 bill. How much change does she receive?
  Answer: 6.00

Example 2:
  Question: A swimming pool is 25 meters long. Tom swims 12 laps. How many meters does he swim in total?
  Answer: 300

Example 3:
  Question: A class has 30 students. 40% of them passed the exam. How many students passed?
  Answer: 12
```

**Step 4: Write prompt.txt (starter)**

```
You are a math assistant. Answer math word problems concisely.
```

**Step 5: Write evaluate.py (hidden)**

```python
"""Evaluation harness for prompt.txt — DO NOT MODIFY.

Sends 10 math word problems to Claude using the system prompt from prompt.txt.
Evaluates answers against ground truth numeric values.

Requires 'claude' CLI to be installed and authenticated.

Output format (parsed by crucible):
    accuracy: <float>   (0.0 to 1.0)
    correct: <int>
    total: <int>
"""

import subprocess
import sys
import re
import traceback

# 10 math word problems with numeric ground truth answers
# These are NOT shown to the agent (prompt.txt or examples.txt)
TEST_PROBLEMS = [
    ("A store offers 20% discount. An item originally costs $45. What is the sale price in dollars?",
     "36"),
    ("A train travels 240 kilometers in 3 hours. What is its speed in kilometers per hour?",
     "80"),
    ("Sarah has 3 times as many apples as Tom. Together they have 48 apples. How many apples does Tom have?",
     "12"),
    ("A recipe needs 2.5 cups of flour to make 12 cookies. How many cups of flour are needed for 30 cookies?",
     "6.25"),
    ("A water tank is 60% full and contains 180 liters. What is the full capacity of the tank in liters?",
     "300"),
    ("If 15% of a number is 45, what is the number?",
     "300"),
    ("A car travels 150 km at 60 km/h, then 100 km at 50 km/h. What is the total travel time in hours?",
     "4.5"),
    ("The sum of two consecutive even numbers is 46. What is the larger number?",
     "24"),
    ("A rectangular garden is 8 meters wide and 12 meters long. How many meters of fencing are needed to enclose it?",
     "40"),
    ("A worker earns $18 per hour and works 7.5 hours. How much does the worker earn in total dollars?",
     "135"),
]

MAX_PROMPT_LENGTH = 2000


def check_prompt(prompt_text: str) -> str | None:
    """Return error if prompt violates constraints."""
    if len(prompt_text) > MAX_PROMPT_LENGTH:
        return f"Prompt too long: {len(prompt_text)} chars > {MAX_PROMPT_LENGTH} limit"
    # Check for hardcoded answers (any of the ground truth values in prompt)
    for _, answer in TEST_PROBLEMS:
        # Simple check: if exact answer appears in prompt as a standalone number
        if re.search(rf'\b{re.escape(answer)}\b', prompt_text):
            pass  # Single number coincidences are OK; only flag obvious lists
    return None


def normalize_answer(text: str) -> str | None:
    """Extract numeric answer from model output."""
    # Look for patterns like "Q1: 36" or "36" or "Answer: 36"
    # Remove currency symbols, commas
    text = text.replace("$", "").replace(",", "").strip()
    # Find all numbers in the text
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
    if numbers:
        return numbers[-1]  # Take last number found
    return None


def answers_match(predicted: str | None, expected: str) -> bool:
    if predicted is None:
        return False
    try:
        pred_val = float(predicted)
        exp_val = float(expected)
        # Allow small floating point tolerance
        return abs(pred_val - exp_val) < 0.01
    except ValueError:
        return predicted.strip() == expected.strip()


def main():
    try:
        with open("prompt.txt", "r") as f:
            system_prompt = f.read().strip()

        violation = check_prompt(system_prompt)
        if violation:
            print(f"VIOLATION: {violation}")
            print("accuracy: 0.0")
            return

        # Build batch prompt: ask all questions at once
        questions_text = "\n\n".join(
            f"Q{i+1}: {q}" for i, (q, _) in enumerate(TEST_PROBLEMS)
        )
        user_message = (
            questions_text
            + "\n\nAnswer each question with just the number. "
            + "Format: Q1: <number> Q2: <number> ... Q10: <number>"
        )

        result = subprocess.run(
            ["claude", "-p", system_prompt, user_message],
            capture_output=True,
            text=True,
            timeout=50,
        )

        if result.returncode != 0:
            print(f"ERROR: claude CLI failed: {result.stderr[:200]}")
            print("accuracy: 0.0")
            return

        output = result.stdout

        # Parse Q1: ... Q10: answers
        correct = 0
        total = len(TEST_PROBLEMS)
        for i, (_, expected) in enumerate(TEST_PROBLEMS):
            # Look for "Q{i+1}: <answer>"
            match = re.search(rf"Q{i+1}:\s*([^\n]+)", output)
            if match:
                predicted = normalize_answer(match.group(1))
            else:
                predicted = None
            if answers_match(predicted, expected):
                correct += 1

        accuracy = correct / total
        print(f"accuracy: {accuracy:.2f}")
        print(f"correct: {correct}")
        print(f"total: {total}")

    except subprocess.TimeoutExpired:
        print("ERROR: claude CLI timed out")
        print("accuracy: 0.0")
    except FileNotFoundError:
        print("ERROR: 'claude' CLI not found — ensure Claude Code is installed")
        print("accuracy: 0.0")
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("accuracy: 0.0")


if __name__ == "__main__":
    main()
```

**Step 6: Write config.yaml**

```yaml
name: "optimize-prompt-math"

files:
  editable:
    - "prompt.txt"
  readonly:
    - "examples.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "accuracy"
  direction: "maximize"

constraints:
  timeout_seconds: 90
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 7: Write program.md**

```markdown
# Prompt Optimization — Math

You are writing a system prompt that helps Claude solve math word problems accurately.

## Goal

Maximize `accuracy` on 10 hidden math word problems (0.0 to 1.0).
A score of 1.0 means Claude answered all 10 problems correctly.

## Setup

- `prompt.txt` contains the system prompt you are optimizing
- The evaluator sends your prompt to Claude with a batch of 10 math problems
- Answers are checked against exact numeric ground truth values

## Rules

- Edit only `prompt.txt`
- Prompt must be plain text (no code, no special syntax)
- Prompt length ≤ 2000 characters
- Do NOT hardcode specific answers in your prompt

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `prompt.txt`

## Hint

See `examples.txt` for 3 sample problems (different from the test set).
The test problems involve: percentages, rates, ratios, mixtures, geometry basics.
Think about how to instruct Claude to: show its reasoning, extract numeric answers cleanly.
```

**Step 8: Smoke test**

```bash
cd src/crucible/examples/optimize-prompt-math && python3 evaluate.py
```

Expected: accuracy around 0.4–0.7 for the minimal starter prompt.

**Step 9: Commit**

```bash
git add src/crucible/examples/optimize-prompt-math/
git commit -m "feat: add optimize-prompt-math example"
```

---

## Task 5: optimize-prompt-logic

Same structure as optimize-prompt-math. Key differences below.

**examples.txt:**

```
# Sample logic reasoning problems (EXAMPLES ONLY — not the test set)
# Answers are: True, False, or Cannot determine

Example 1:
  Problem: All birds have wings. Penguins are birds. Do penguins have wings?
  Answer: True

Example 2:
  Problem: No reptile is warm-blooded. A gecko is a reptile. Is a gecko warm-blooded?
  Answer: False

Example 3:
  Problem: Some athletes are vegetarians. Some vegetarians are doctors. Are some athletes doctors?
  Answer: Cannot determine
```

**prompt.txt starter:**

```
You are a logic assistant. Evaluate logical arguments carefully.
```

**TEST_PROBLEMS in evaluate.py** (10 problems):

```python
TEST_PROBLEMS = [
    ("All cats are mammals. Whiskers is a cat. Is Whiskers a mammal?",
     "True"),
    ("No fish can breathe air. Nemo is a fish. Can Nemo breathe air?",
     "False"),
    ("Some students are athletes. Some athletes are musicians. Are some students musicians?",
     "Cannot determine"),
    ("If it rains, the ground gets wet. The ground is wet. Did it rain?",
     "Cannot determine"),
    ("All squares are rectangles. All rectangles have four sides. Do all squares have four sides?",
     "True"),
    ("Every dog is an animal. Rex is not an animal. Is Rex a dog?",
     "False"),
    ("If A implies B, and B implies C, and A is true, is C true?",
     "True"),
    ("All members of Club X speak French. Marie speaks French. Is Marie a member of Club X?",
     "Cannot determine"),
    ("If today is Monday, the shop is closed. The shop is open. Is today Monday?",
     "False"),
    ("Some fruits are sweet. All apples are fruits. Are all apples sweet?",
     "Cannot determine"),
]
```

**Answer matching** — normalize to one of three labels:

```python
def normalize_answer(text: str) -> str | None:
    text = text.strip().lower()
    if "cannot" in text or "can't" in text or "undetermined" in text or "uncertain" in text:
        return "Cannot determine"
    if "true" in text or "yes" in text:
        return "True"
    if "false" in text or "no" in text:
        return "False"
    return None

def answers_match(predicted, expected):
    if predicted is None:
        return False
    return predicted.lower() == expected.lower()
```

**User message format:**

```python
user_message = (
    questions_text
    + "\n\nAnswer each with exactly one of: True, False, or Cannot determine. "
    + "Format: Q1: <answer> Q2: <answer> ... Q10: <answer>"
)
```

**config.yaml:** same as math but `name: "optimize-prompt-logic"`.

**Step: Commit**

```bash
git add src/crucible/examples/optimize-prompt-logic/
git commit -m "feat: add optimize-prompt-logic example"
```

---

## Task 6: optimize-prompt-format

Same structure. Key differences:

**examples.txt:**

```
# Sample format conversion tasks (EXAMPLES ONLY — not the test set)
# Convert the input to the exact specified output format.

Example 1:
  Input: Convert date to ISO format: "January 5, 2024"
  Expected output: 2024-01-05

Example 2:
  Input: Convert to centimeters (2 decimal places): "6 feet 0 inches"
  Expected output: 182.88 cm

Example 3:
  Input: Normalize phone to international format: "0922 111 222"
  Expected output: +886-922-111-222
```

**prompt.txt starter:**

```
You are a format conversion assistant. Convert inputs to the exact specified format.
```

**TEST_PROBLEMS in evaluate.py** (10 tasks, exact string match):

```python
TEST_PROBLEMS = [
    ('Convert date to ISO format (YYYY-MM-DD): "15 April 2024"',
     "2024-04-15"),
    ('Convert date to ISO format (YYYY-MM-DD): "March 3, 2025"',
     "2025-03-03"),
    ('Convert to centimeters, rounded to 2 decimal places: "5 feet 11 inches"',
     "180.34 cm"),
    ('Convert to centimeters, rounded to 2 decimal places: "6 feet 2 inches"',
     "187.96 cm"),
    ('Convert Fahrenheit to Celsius, rounded to 2 decimal places: "32 degrees Fahrenheit"',
     "0.00 degrees Celsius"),
    ('Convert Fahrenheit to Celsius, rounded to 2 decimal places: "212 degrees Fahrenheit"',
     "100.00 degrees Celsius"),
    ('Normalize Taiwan landline to international format: "(02) 1234-5678"',
     "+886-2-1234-5678"),
    ('Normalize Taiwan mobile to international format: "0912-345-678"',
     "+886-912-345-678"),
    ('Remove thousands separators and currency symbol, keep 2 decimal places: "$1,234.50"',
     "1234.50"),
    ('Remove thousands separators and currency symbol, keep 2 decimal places: "$98,765.00"',
     "98765.00"),
]
```

**Answer matching** — exact string match after strip:

```python
def answers_match(predicted, expected):
    if predicted is None:
        return False
    return predicted.strip() == expected.strip()

def normalize_answer(text):
    # Return the full response stripped, since we need exact match
    return text.strip()
```

**User message format:**

```python
user_message = (
    "\n\n".join(f"Q{i+1}: {q}" for i, (q, _) in enumerate(TEST_PROBLEMS))
    + "\n\nOutput ONLY the converted result for each question, nothing else. "
    + "Format: Q1: <result> Q2: <result> ... Q10: <result>"
)
```

Parse responses by extracting content after `Q{i+1}: ` up to next `Q` or end.

**Step: Commit**

```bash
git add src/crucible/examples/optimize-prompt-format/
git commit -m "feat: add optimize-prompt-format example"
```

---

## Task 7: optimize-quantize

**Files:**
- Create: `src/crucible/examples/optimize-quantize/quantize.py`
- Create: `src/crucible/examples/optimize-quantize/model_info.txt`
- Create: `src/crucible/examples/optimize-quantize/scripts/generate_model.py`
- Create: `src/crucible/examples/optimize-quantize/evaluate.py`
- Create: `src/crucible/examples/optimize-quantize/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-quantize/.crucible/program.md`
- Create: `src/crucible/examples/optimize-quantize/.gitignore`

**Step 1: Create directories**

```bash
mkdir -p src/crucible/examples/optimize-quantize/.crucible
mkdir -p src/crucible/examples/optimize-quantize/scripts
```

**Step 2: Write scripts/generate_model.py and run it**

```python
"""Generate a pre-trained 3-layer MLP classifier and save as model.npz.

Run once: python3 scripts/generate_model.py
This creates model.npz in the project root directory.
"""

import numpy as np

SEED = 42
rng = np.random.default_rng(SEED)

# Architecture: 20 features → 64 → 32 → 5 classes
N_FEATURES = 20
HIDDEN1 = 64
HIDDEN2 = 32
N_CLASSES = 5

# Generate synthetic training data
N_TRAIN = 2000
N_TEST = 500

# Each class has a different mean vector
class_means = rng.normal(0, 1, (N_CLASSES, N_FEATURES))
X_train = np.vstack([
    rng.normal(class_means[c], 0.8, (N_TRAIN // N_CLASSES, N_FEATURES))
    for c in range(N_CLASSES)
])
y_train = np.repeat(np.arange(N_CLASSES), N_TRAIN // N_CLASSES)

X_test = np.vstack([
    rng.normal(class_means[c], 0.8, (N_TEST // N_CLASSES, N_FEATURES))
    for c in range(N_CLASSES)
])
y_test = np.repeat(np.arange(N_CLASSES), N_TEST // N_CLASSES)

# Initialize weights (Xavier)
def xavier(fan_in, fan_out, rng):
    scale = np.sqrt(2.0 / (fan_in + fan_out))
    return rng.normal(0, scale, (fan_in, fan_out)).astype(np.float32)

W1 = xavier(N_FEATURES, HIDDEN1, rng)
b1 = np.zeros(HIDDEN1, dtype=np.float32)
W2 = xavier(HIDDEN1, HIDDEN2, rng)
b2 = np.zeros(HIDDEN2, dtype=np.float32)
W3 = xavier(HIDDEN2, N_CLASSES, rng)
b3 = np.zeros(N_CLASSES, dtype=np.float32)

def relu(x): return np.maximum(0, x)
def softmax(x):
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)

def forward(X, W1, b1, W2, b2, W3, b3):
    h1 = relu(X @ W1 + b1)
    h2 = relu(h1 @ W2 + b2)
    return softmax(h2 @ W3 + b3)

# Simple SGD training
LR = 0.01
for epoch in range(200):
    idx = rng.permutation(len(X_train))
    for i in range(0, len(X_train), 64):
        batch_idx = idx[i:i+64]
        X_b = X_train[batch_idx].astype(np.float32)
        y_b = y_train[batch_idx]

        # Forward
        h1 = relu(X_b @ W1 + b1)
        h2 = relu(h1 @ W2 + b2)
        logits = h2 @ W3 + b3
        probs = softmax(logits)

        # Loss gradient
        dlogits = probs.copy()
        dlogits[np.arange(len(y_b)), y_b] -= 1
        dlogits /= len(y_b)

        # Backprop W3, b3
        dW3 = h2.T @ dlogits
        db3 = dlogits.sum(0)
        dh2 = dlogits @ W3.T
        dh2[h2 == 0] = 0  # ReLU grad

        dW2 = h1.T @ dh2
        db2 = dh2.sum(0)
        dh1 = dh2 @ W2.T
        dh1[h1 == 0] = 0

        dW1 = X_b.T @ dh1
        db1 = dh1.sum(0)

        W1 -= LR * dW1; b1 -= LR * db1
        W2 -= LR * dW2; b2 -= LR * db2
        W3 -= LR * dW3; b3 -= LR * db3

# Evaluate
probs = forward(X_test.astype(np.float32), W1, b1, W2, b2, W3, b3)
preds = probs.argmax(axis=1)
acc = (preds == y_test).mean()
print(f"Model accuracy: {acc:.4f}")

# Save
np.savez("model.npz",
         W1=W1, b1=b1, W2=W2, b2=b2, W3=W3, b3=b3,
         X_test=X_test.astype(np.float32), y_test=y_test,
         class_means=class_means.astype(np.float32))
print("Saved model.npz")
print(f"W1: {W1.shape}, W2: {W2.shape}, W3: {W3.shape}")
```

Run: `cd src/crucible/examples/optimize-quantize && python3 scripts/generate_model.py`

Commit `model.npz` into git (it's ~50KB).

**Step 3: Write model_info.txt**

```
# Model Architecture

A 3-layer MLP classifier trained on a 5-class synthetic dataset.

Layers:
  W1: (20, 64)  float32  — input layer weights
  b1: (64,)     float32  — input layer biases
  W2: (64, 32)  float32  — hidden layer weights
  b2: (32,)     float32  — hidden layer biases
  W3: (32, 5)   float32  — output layer weights
  b3: (5,)      float32  — output layer biases

Activation: ReLU (hidden layers), Softmax (output)
Baseline accuracy (no quantization): ~90%
Total parameters: 20×64 + 64 + 64×32 + 32 + 32×5 + 5 = 1,280 + 64 + 2,048 + 32 + 160 + 5 = 3,589
Total weights (excluding biases): 3,488 float32 values = 13,952 bytes

Quantization target: reduce avg bits per weight while preserving accuracy.
Score = accuracy × (32 / avg_bits_per_weight)
  - 32-bit (no quantization): score = 0.90 × 1.0 = 0.90
  - INT8 (8-bit): score ≈ 0.89 × 4.0 = 3.56
  - INT4 (4-bit): score ≈ 0.87 × 8.0 = 6.96 (if accuracy holds)
  - INT2 (2-bit): likely big accuracy drop
```

**Step 4: Write quantize.py (starter — no quantization, 32-bit passthrough)**

```python
"""Quantization implementation — edit this file.

Interface:
  quantize(weights: np.ndarray, layer_name: str) -> dict
      Quantize a weight array. Return a dict with at least:
        - 'data': the quantized representation (any format)
        - 'bits': average bits per original weight (float)
        - 'layer': layer_name (str)
      Include any other fields needed by dequantize().

  dequantize(q: dict) -> np.ndarray
      Reconstruct float32 weights from quantized representation.
      Must return array of same shape and dtype=float32 as original.

Baseline: identity (no quantization) — 32 bits/weight, score = accuracy × 1.0
Goal: reduce bits/weight while keeping accuracy high.
Score = accuracy × (32 / avg_bits_per_weight)

Dependencies: numpy only (no torch, no bitsandbytes)
"""

import numpy as np


def quantize(weights: np.ndarray, layer_name: str) -> dict:
    """Quantize weights. Baseline: store as-is (32-bit float)."""
    return {
        "data": weights.astype(np.float32).copy(),
        "shape": weights.shape,
        "bits": 32.0,
        "layer": layer_name,
    }


def dequantize(q: dict) -> np.ndarray:
    """Reconstruct float32 weights from quantized dict."""
    return q["data"].reshape(q["shape"]).astype(np.float32)
```

**Step 5: Write evaluate.py (hidden)**

```python
"""Evaluation harness for quantize.py — DO NOT MODIFY.

Loads model.npz, quantizes all weight matrices using quantize.py,
reconstructs them, runs inference, measures accuracy and compression.

Output format (parsed by crucible):
    score: <float>    (accuracy × 32 / avg_bits, higher = better)
    accuracy: <float>
    avg_bits: <float>
"""

import numpy as np
import sys
import traceback


def relu(x):
    return np.maximum(0, x)


def softmax(x):
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


def forward(X, params):
    W1, b1, W2, b2, W3, b3 = params
    h1 = relu(X @ W1 + b1)
    h2 = relu(h1 @ W2 + b2)
    return softmax(h2 @ W3 + b3)


def main():
    try:
        from quantize import quantize, dequantize

        data = np.load("model.npz")
        X_test = data["X_test"]
        y_test = data["y_test"]

        # Weight matrices to quantize (biases stay at float32)
        weight_keys = ["W1", "W2", "W3"]
        total_bits = 0.0
        total_params = 0
        reconstructed = {}

        for key in weight_keys:
            w = data[key]
            q = quantize(w.copy(), key)

            if not isinstance(q, dict):
                print(f"ERROR: quantize() must return dict, got {type(q)}")
                print("score: 0.0")
                return
            if "bits" not in q:
                print("ERROR: quantize() dict must contain 'bits' key")
                print("score: 0.0")
                return

            bits = float(q["bits"])
            if bits <= 0 or bits > 32:
                print(f"ERROR: bits must be in (0, 32], got {bits}")
                print("score: 0.0")
                return

            w_reconstructed = dequantize(q)
            if w_reconstructed.shape != w.shape:
                print(f"ERROR: dequantize shape mismatch for {key}: "
                      f"expected {w.shape}, got {w_reconstructed.shape}")
                print("score: 0.0")
                return

            reconstructed[key] = w_reconstructed.astype(np.float32)
            total_bits += bits * w.size
            total_params += w.size

        avg_bits = total_bits / total_params

        # Run inference with quantized weights
        params = (
            reconstructed["W1"], data["b1"],
            reconstructed["W2"], data["b2"],
            reconstructed["W3"], data["b3"],
        )
        probs = forward(X_test, params)
        preds = probs.argmax(axis=1)
        accuracy = float((preds == y_test).mean())

        score = accuracy * (32.0 / avg_bits)

        print(f"score: {score:.4f}")
        print(f"accuracy: {accuracy:.4f}")
        print(f"avg_bits: {avg_bits:.4f}")
        print(f"total_params: {total_params}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("score: 0.0")


if __name__ == "__main__":
    main()
```

**Step 6: config.yaml**

```yaml
name: "optimize-quantize"

files:
  editable:
    - "quantize.py"
  readonly:
    - "model.npz"
    - "model_info.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "score"
  direction: "maximize"

constraints:
  timeout_seconds: 60
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 7: program.md**

```markdown
# Quantization Optimization

You are implementing post-training quantization to compress a neural network.

## Goal

Maximize `score = accuracy × (32 / avg_bits_per_weight)`.
- Higher compression (fewer bits) = higher multiplier
- But compression hurts accuracy
- Find the best accuracy/compression trade-off

## Interface

```python
def quantize(weights: np.ndarray, layer_name: str) -> dict:
    # Returns dict with 'data', 'bits' (float), 'layer', and any fields dequantize needs

def dequantize(q: dict) -> np.ndarray:
    # Reconstructs float32 array of same shape as original
```

## Rules

- Edit only `quantize.py`
- numpy only — no torch, bitsandbytes, or other ML libraries
- `dequantize(quantize(w))` must return array of same shape as `w`
- `bits` must be a float in (0.0, 32.0]

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `quantize.py`

## Model Info

See `model_info.txt` for layer shapes and expected score ranges.
Baseline (no quantization, 32-bit): score ≈ 0.90

## Strategy

Common quantization approaches:
- **INT8 symmetric**: scale weights to [-127, 127], store as int8 (8 bits) → score ≈ 3.5×
- **INT8 asymmetric**: use zero-point offset for better accuracy
- **INT4**: 4-bit quantization → score ≈ 7×, more accuracy loss
- **Mixed precision**: quantize large layers more aggressively, keep small layers at higher precision
- **Per-channel quantization**: separate scale per output neuron for better accuracy
```

**Step 8: Smoke test**

```bash
cd src/crucible/examples/optimize-quantize && python3 scripts/generate_model.py && python3 evaluate.py
```

**Step 9: Commit**

```bash
git add src/crucible/examples/optimize-quantize/
git commit -m "feat: add optimize-quantize example"
```

---

## Task 8: optimize-rl-policy

**Files:**
- Create: `src/crucible/examples/optimize-rl-policy/policy.py`
- Create: `src/crucible/examples/optimize-rl-policy/obs_info.txt`
- Create: `src/crucible/examples/optimize-rl-policy/evaluate.py`
- Create: `src/crucible/examples/optimize-rl-policy/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-rl-policy/.crucible/program.md`
- Create: `src/crucible/examples/optimize-rl-policy/.gitignore`

**Step 1: Create directory**

```bash
mkdir -p src/crucible/examples/optimize-rl-policy/.crucible
```

**Step 2: Write obs_info.txt**

```
# CartPole Observation Space

The pole-balancing environment provides 4 observations per step:

  obs[0]: cart_position      — position of cart on track, range [-4.8, 4.8] meters
  obs[1]: cart_velocity      — cart velocity (meters per second)
  obs[2]: pole_angle         — pole angle from vertical, range [-0.418, 0.418] radians (~24 degrees)
  obs[3]: pole_angular_vel   — pole angular velocity (radians per second)

Actions:
  0 = push cart LEFT  (force = -10 N)
  1 = push cart RIGHT (force = +10 N)

Episode ends when:
  - |cart_position| > 2.4 meters (cart fell off track), OR
  - |pole_angle| > 12 degrees (pole fell over), OR
  - 500 steps reached (success)

Score = mean steps survived across 200 episodes.
Baseline (random): ~20 steps.
Simple heuristic (push in direction pole is falling): ~150 steps.
Good policy (LQR / linear): ~400-500 steps.
```

**Step 3: Write policy.py (starter — random policy)**

```python
"""CartPole policy — edit this file to improve mean_steps.

Interface:
  select_action(obs: list[float]) -> int
      obs = [cart_position, cart_velocity, pole_angle, pole_angular_vel]
      Return 0 (push left) or 1 (push right).

Goal: maximize mean episode length across 200 episodes.
Baseline (random): ~20 steps. Good policy: 400-500 steps.

Constraints:
  - No gym, torch, or numpy imports (pure Python + math/random)
  - Policy must be deterministic (reproducible given same obs sequence)
"""

import random as _random

_rng = _random.Random(99)


def select_action(obs: list[float]) -> int:
    """Baseline: random action."""
    return _rng.randint(0, 1)
```

**Step 4: Write evaluate.py (hidden, includes full CartPole physics)**

```python
"""Evaluation harness for policy.py — DO NOT MODIFY.

Implements CartPole physics from scratch (no gym dependency).
Runs 200 episodes and computes mean steps survived.

Physics parameters match OpenAI Gym CartPole-v1.

Output format (parsed by crucible):
    mean_steps: <float>
    min_steps: <int>
    max_steps: <int>
    episodes: <int>
"""

import math
import random
import sys
import traceback

# CartPole physics constants (from OpenAI Gym CartPole-v1)
GRAVITY = 9.8
MASS_CART = 1.0
MASS_POLE = 0.1
TOTAL_MASS = MASS_CART + MASS_POLE
POLE_HALF_LENGTH = 0.5
POLE_MASS_LENGTH = MASS_POLE * POLE_HALF_LENGTH
FORCE_MAG = 10.0
TAU = 0.02  # seconds per step

# Failure thresholds
X_THRESHOLD = 2.4
ANGLE_THRESHOLD_RAD = 12 * math.pi / 180  # 12 degrees

MAX_STEPS = 500
N_EPISODES = 200
SEED = 777


def cartpole_step(state, action):
    """Advance CartPole physics by one timestep. Returns (new_state, done)."""
    x, x_dot, theta, theta_dot = state
    force = FORCE_MAG if action == 1 else -FORCE_MAG

    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)

    temp = (force + POLE_MASS_LENGTH * theta_dot ** 2 * sin_theta) / TOTAL_MASS
    theta_acc = (GRAVITY * sin_theta - cos_theta * temp) / (
        POLE_HALF_LENGTH * (4.0 / 3.0 - MASS_POLE * cos_theta ** 2 / TOTAL_MASS)
    )
    x_acc = temp - POLE_MASS_LENGTH * theta_acc * cos_theta / TOTAL_MASS

    # Euler integration
    x = x + TAU * x_dot
    x_dot = x_dot + TAU * x_acc
    theta = theta + TAU * theta_dot
    theta_dot = theta_dot + TAU * theta_acc

    done = bool(
        abs(x) > X_THRESHOLD
        or abs(theta) > ANGLE_THRESHOLD_RAD
    )
    return [x, x_dot, theta, theta_dot], done


def run_episode(policy_fn, rng, episode_seed):
    """Run one CartPole episode. Returns steps survived."""
    ep_rng = random.Random(episode_seed)
    state = [ep_rng.uniform(-0.05, 0.05) for _ in range(4)]

    for step in range(MAX_STEPS):
        action = policy_fn(list(state))
        if action not in (0, 1):
            return 0  # invalid action
        state, done = cartpole_step(state, action)
        if done:
            return step + 1

    return MAX_STEPS


def main():
    try:
        from policy import select_action

        rng = random.Random(SEED)
        episode_seeds = [rng.randint(0, 10**9) for _ in range(N_EPISODES)]

        steps_list = []
        for seed in episode_seeds:
            steps = run_episode(select_action, rng, seed)
            steps_list.append(steps)

        mean_steps = sum(steps_list) / len(steps_list)
        print(f"mean_steps: {mean_steps:.2f}")
        print(f"min_steps: {min(steps_list)}")
        print(f"max_steps: {max(steps_list)}")
        print(f"episodes: {N_EPISODES}")
        print(f"perfect_episodes: {sum(1 for s in steps_list if s >= MAX_STEPS)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("mean_steps: 0.0")


if __name__ == "__main__":
    main()
```

**Step 5: config.yaml**

```yaml
name: "optimize-rl-policy"

files:
  editable:
    - "policy.py"
  readonly:
    - "obs_info.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "mean_steps"
  direction: "maximize"

constraints:
  timeout_seconds: 60
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 6: program.md**

```markdown
# CartPole Policy Optimization

You are designing a policy to balance a pole on a moving cart.

## Goal

Maximize `mean_steps` — average steps survived across 200 episodes.
Maximum possible: 500 steps per episode.
Baseline (random policy): ~20 steps.

## Interface

```python
def select_action(obs: list[float]) -> int:
    # obs = [cart_position, cart_velocity, pole_angle, pole_angular_vel]
    # Return 0 (push LEFT) or 1 (push RIGHT)
```

## Rules

- Edit only `policy.py`
- No gym, torch, or numpy — pure Python + `math` and `random` stdlib
- Return must be 0 or 1
- Policy should be deterministic (no external state changes between episodes)

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `policy.py`

## Observation Space

See `obs_info.txt` for full details on observations and physics.

## Strategy

Start simple: push in the direction the pole is leaning.
```python
# Heuristic: follow the pole angle
return 1 if obs[2] > 0 else 0
```
This gets ~150 steps. To reach 400+, consider using pole_angular_vel (obs[3]) too.
Linear combination of all 4 observations (LQR coefficients) can reach near-perfect.
```

**Step 7: Smoke test**

```bash
cd src/crucible/examples/optimize-rl-policy && python3 evaluate.py
```

Expected: mean_steps around 15–25 for random policy.

**Step 8: Commit**

```bash
git add src/crucible/examples/optimize-rl-policy/
git commit -m "feat: add optimize-rl-policy example"
```

---

## Task 9: optimize-codegen

**Files:**
- Create: `src/crucible/examples/optimize-codegen/generator.py`
- Create: `src/crucible/examples/optimize-codegen/spec_schema.txt`
- Create: `src/crucible/examples/optimize-codegen/evaluate.py`
- Create: `src/crucible/examples/optimize-codegen/.crucible/config.yaml`
- Create: `src/crucible/examples/optimize-codegen/.crucible/program.md`
- Create: `src/crucible/examples/optimize-codegen/.gitignore`

**Step 1: Create directory**

```bash
mkdir -p src/crucible/examples/optimize-codegen/.crucible
```

**Step 2: Write spec_schema.txt**

```
# Code Generator Spec Schema

Your generator receives a `spec` dict describing a computational task.
It must return a Python code string that:
  1. Computes the answer
  2. Assigns it to a variable named `result`

Spec format:
  {"task": "<task_name>", ...parameters...}

Two example specs (the full test set has 10 different specs):

Example 1:
  spec = {"task": "sum_of_squares", "n": 1000}
  # Compute sum of i² for i in 1..n
  # result should be an integer: 333833500

Example 2:
  spec = {"task": "count_vowels", "text": "Hello World"}
  # Count vowels (a,e,i,o,u, case-insensitive)
  # result should be an integer: 3

Your generated code runs in a restricted namespace.
Forbidden imports: ctypes, subprocess, os, sys, open, socket, urllib.
Allowed: math, itertools, collections, functools, and any pure Python.

Scoring per task:
  - correctness: 1.0 if result == expected, else 0.0
  - speed_ratio: min(reference_time / your_time, 10.0)
  - task_score: correctness × speed_ratio
Final score: mean task_score across all 10 tasks.
```

**Step 3: Write generator.py (starter — naive implementations)**

```python
"""Code generator — edit this file to improve score.

Interface:
  generate(spec: dict) -> str
      Given a task specification, return a Python code string.
      The code must assign its answer to a variable named `result`.

Example:
  generate({"task": "sum_of_squares", "n": 100})
  # Should return something like:
  # "result = sum(i*i for i in range(1, 101))"

The generated code is executed and timed.
Score = correctness × speed (compared to reference implementation).
"""


def generate(spec: dict) -> str:
    """Generate Python code for the given task spec."""
    task = spec.get("task", "")

    if task == "sum_of_squares":
        n = spec["n"]
        return f"result = sum(i*i for i in range(1, {n}+1))"

    elif task == "count_vowels":
        text = repr(spec["text"])
        return f"result = sum(1 for c in {text}.lower() if c in 'aeiou')"

    else:
        # Unknown task: return 0
        return "result = 0"
```

**Step 4: Write evaluate.py (hidden, 10 task specs)**

```python
"""Evaluation harness for generator.py — DO NOT MODIFY.

Runs generator.generate(spec) for 10 task specs.
Executes generated code, checks correctness, measures speed.

Output format (parsed by crucible):
    score: <float>     (mean correctness × speed_ratio across 10 tasks)
    correct_tasks: <int>
    total_tasks: <int>
"""

import time
import traceback
import sys

FORBIDDEN_NAMES = frozenset([
    "ctypes", "subprocess", "os", "sys", "open", "socket",
    "urllib", "__import__", "eval", "exec", "compile",
    "breakpoint", "input",
])

MAX_SPEED_RATIO = 10.0
GENERATOR_TIMEOUT = 3.0  # seconds for generate() to produce all code


def safe_exec(code: str, expected_type=None):
    """Execute code in restricted namespace. Returns (result, error)."""
    # Block forbidden imports via __builtins__ restriction
    safe_builtins = {
        k: v for k, v in __builtins__.items()
        if k not in FORBIDDEN_NAMES
    } if isinstance(__builtins__, dict) else {
        k: getattr(__builtins__, k)
        for k in dir(__builtins__)
        if k not in FORBIDDEN_NAMES and not k.startswith("_")
    }
    # Allow import of safe stdlib modules
    import math, itertools, collections, functools, heapq, bisect
    namespace = {
        "__builtins__": safe_builtins,
        "math": math,
        "itertools": itertools,
        "collections": collections,
        "functools": functools,
        "heapq": heapq,
        "bisect": bisect,
    }
    try:
        exec(code, namespace)
        return namespace.get("result"), None
    except Exception as e:
        return None, str(e)


# 10 test specs with reference answers and naive reference implementations
TEST_CASES = [
    {
        "spec": {"task": "sum_of_squares", "n": 100_000},
        "expected": sum(i*i for i in range(1, 100_001)),
        "reference": lambda: sum(i*i for i in range(1, 100_001)),
    },
    {
        "spec": {"task": "count_vowels", "text": "The quick brown fox jumps over the lazy dog " * 200},
        "expected": sum(1 for c in ("The quick brown fox jumps over the lazy dog " * 200).lower() if c in "aeiou"),
        "reference": lambda: sum(1 for c in ("The quick brown fox jumps over the lazy dog " * 200).lower() if c in "aeiou"),
    },
    {
        "spec": {"task": "find_primes", "limit": 10_000},
        "expected": [i for i in range(2, 10_001) if all(i % j != 0 for j in range(2, int(i**0.5)+1))],
        "reference": lambda: [i for i in range(2, 10_001) if all(i % j != 0 for j in range(2, int(i**0.5)+1))],
    },
    {
        "spec": {"task": "flatten", "lst": [[1,[2,3]],[4,[5,[6]]],[7,8]], "depth": 3},
        "expected": [1,2,3,4,5,6,7,8],
        "reference": lambda: [1,2,3,4,5,6,7,8],
    },
    {
        "spec": {"task": "fibonacci", "n": 35},
        "expected": 9227465,
        "reference": lambda: (lambda f: f(f, 35))(lambda f, n: n if n <= 1 else f(f,n-1)+f(f,n-2)),
    },
    {
        "spec": {"task": "word_count", "text": "the cat sat on the mat the cat in the hat " * 500},
        "expected": {"the": 2000, "cat": 1000, "sat": 500, "on": 500, "mat": 500, "in": 500, "hat": 500},
        "reference": lambda: __import__("collections").Counter(("the cat sat on the mat the cat in the hat " * 500).split()),
    },
    {
        "spec": {"task": "matrix_trace", "n": 200},
        "expected": sum(range(0, 200*200, 201)),  # trace of n×n matrix where M[i][j] = i*n+j
        "reference": lambda: sum(range(0, 200*200, 201)),
    },
    {
        "spec": {"task": "run_length_encode", "data": [1]*100 + [2]*50 + [3]*25 + [1]*75},
        "expected": [(1,100),(2,50),(3,25),(1,75)],
        "reference": lambda: [(k, sum(1 for _ in g)) for k,g in __import__("itertools").groupby([1]*100+[2]*50+[3]*25+[1]*75)],
    },
    {
        "spec": {"task": "gcd", "numbers": [48, 18, 36, 24, 72, 12, 96]},
        "expected": 6,
        "reference": lambda: __import__("math").gcd(*[48,18,36,24,72,12,96]),
    },
    {
        "spec": {"task": "anagram_groups", "words": ["eat","tea","tan","ate","nat","bat"]},
        "expected": sorted([sorted(["eat","tea","ate"]), sorted(["tan","nat"]), ["bat"]]),
        "reference": lambda: sorted([sorted(v) for v in __import__("collections").defaultdict(list, {tuple(sorted(w)): [] for w in ["eat","tea","tan","ate","nat","bat"]}).values()]),
    },
]


def time_fn(fn, reps=3):
    """Run fn reps times, return minimum elapsed seconds."""
    best = float("inf")
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - t0)
    return best


def results_equal(a, b) -> bool:
    """Compare results, handling lists/dicts."""
    if type(a) != type(b):
        try:
            return sorted(a) == sorted(b)
        except Exception:
            return str(a) == str(b)
    if isinstance(a, list):
        return sorted(str(x) for x in a) == sorted(str(x) for x in b)
    return a == b


def main():
    try:
        from generator import generate

        total_score = 0.0
        correct_tasks = 0

        for i, tc in enumerate(TEST_CASES):
            spec = tc["spec"]
            expected = tc["expected"]
            ref_fn = tc["reference"]

            # Generate code
            try:
                t0 = time.perf_counter()
                code = generate(spec)
                gen_time = time.perf_counter() - t0
            except Exception as e:
                print(f"Task {i+1} ({spec['task']}): generate() error: {e}")
                continue

            if not isinstance(code, str):
                print(f"Task {i+1}: generate() must return str")
                continue

            # Execute and check correctness
            result, error = safe_exec(code)
            if error:
                print(f"Task {i+1} ({spec['task']}): exec error: {error}")
                continue

            correct = results_equal(result, expected)
            if not correct:
                print(f"Task {i+1} ({spec['task']}): wrong answer (got {type(result).__name__})")
                continue

            correct_tasks += 1

            # Time the generated code vs reference
            def run_generated():
                safe_exec(code)

            try:
                generated_time = time_fn(run_generated, reps=3)
                ref_time = time_fn(ref_fn, reps=3)
                speed_ratio = min(ref_time / generated_time, MAX_SPEED_RATIO) if generated_time > 0 else 1.0
            except Exception:
                speed_ratio = 1.0

            task_score = 1.0 * speed_ratio
            total_score += task_score
            print(f"Task {i+1} ({spec['task']}): correct, speed_ratio={speed_ratio:.2f}, score={task_score:.2f}")

        mean_score = total_score / len(TEST_CASES)
        print(f"score: {mean_score:.4f}")
        print(f"correct_tasks: {correct_tasks}")
        print(f"total_tasks: {len(TEST_CASES)}")

    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        print("score: 0.0")


if __name__ == "__main__":
    main()
```

**Step 5: config.yaml**

```yaml
name: "optimize-codegen"

files:
  editable:
    - "generator.py"
  readonly:
    - "spec_schema.txt"
  hidden:
    - "evaluate.py"

commands:
  run: "python3 -u evaluate.py 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "score"
  direction: "maximize"

constraints:
  timeout_seconds: 90
  max_retries: 5

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

**Step 6: program.md**

```markdown
# Code Generator Optimization

You are writing a code generator that produces Python code for computational tasks.

## Goal

Maximize `score = mean(correctness × speed_ratio)` across 10 task specs.
- Correctness: 1.0 if `result` variable has correct value, else 0.0
- Speed ratio: min(reference_time / your_time, 10.0) — faster than reference = higher score

## Interface

```python
def generate(spec: dict) -> str:
    """Return Python code as a string.
    The code must assign the answer to a variable named `result`.
    """
```

## Rules

- Edit only `generator.py`
- Generated code cannot import: ctypes, subprocess, os, sys, socket, urllib
- Generated code may use: math, itertools, collections, functools, heapq, bisect
- `generate()` must produce all 10 code strings in under 3 seconds total

## Hard Rules

- DO NOT attempt to run or execute any scripts — the platform runs them automatically
- DO NOT modify any file other than `generator.py`

## Task Types

See `spec_schema.txt` for the spec format and 2 example tasks.
The full test set has 10 different computational tasks.

## Strategy

For each known task type, generate the most efficient implementation:
- Use math formulas instead of loops where possible (e.g., sum of squares = n(n+1)(2n+1)/6)
- Use stdlib optimized functions (itertools.groupby, collections.Counter)
- Use Sieve of Eratosthenes instead of trial division for primes
```

**Step 7: Smoke test**

```bash
cd src/crucible/examples/optimize-codegen && python3 evaluate.py
```

Expected: score around 1.0–3.0 for starter (2 known tasks correct, basic speed).

**Step 8: Commit**

```bash
git add src/crucible/examples/optimize-codegen/
git commit -m "feat: add optimize-codegen example"
```

---

## Task 10: Register all examples in examples list

**Step 1: Find examples registration**

```bash
grep -r "optimize-sorting" src/crucible/ --include="*.py" --include="*.md" --include="*.yaml" -l
```

**Step 2: Add to any examples index/list**

Check if `src/crucible/examples/__init__.py` or similar exists. If there's an `examples.md` or registry, add all 9 new examples to it.

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: register 9 new crucible examples in index"
```

---

## Validation Checklist

Run each example manually before considering it complete:

```bash
# For each example:
cd src/crucible/examples/<name>
python3 evaluate.py         # Should print metric: <value>
crucible validate           # Should pass validation

# For prompt examples: verify claude CLI works
claude --version            # Must succeed
```

Expected baseline scores:
| Example | Metric | Baseline |
|---------|--------|----------|
| optimize-tokenizer | tokens_per_char | 1.0000 |
| optimize-prompt-math | accuracy | 0.40–0.70 |
| optimize-prompt-logic | accuracy | 0.40–0.70 |
| optimize-prompt-format | accuracy | 0.30–0.60 |
| optimize-regex | f1_score | 0.65–0.75 |
| optimize-hash | uniformity_score | 0.50–0.80 |
| optimize-quantize | score | 0.85–0.95 |
| optimize-rl-policy | mean_steps | 15–25 |
| optimize-codegen | score | 1.0–3.0 |
