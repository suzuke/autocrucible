# 使用 Crucible 優化 Rust 專案：以 Parry 碰撞偵測為例

本指南說明如何使用 Crucible 自動優化 Rust 專案的效能，以 rapier/parry 物理引擎的 cylinder-cylinder 碰撞偵測為實際案例。Crucible 是一個自主實驗平台，透過 generate-edit-evaluate 迴圈反覆優化指定的 metric。現有的 Crucible 範例皆為 Python 專案，本指南涵蓋 Rust 專案的設定與注意事項。

案例成果：在 6 次迭代中，agent 自主將 cylinder-cylinder 碰撞偵測的 benchmark 提速 6.4%。

---

## 1. 前置條件

- **Rust toolchain**：需要 stable 與 nightly 兩個 toolchain，透過 [rustup](https://rustup.rs/) 安裝：

  ```bash
  rustup install stable
  rustup install nightly
  ```

- **Crucible CLI**：

  ```bash
  uv tool install crucible
  ```

- **macOS Homebrew 衝突警告**：如果你透過 Homebrew 安裝過 Rust，`/opt/homebrew/bin/cargo` 會覆蓋 rustup 管理的 cargo。這會導致 `cargo +nightly`、`rustup run nightly cargo`、`RUSTUP_TOOLCHAIN=nightly` 等指令全部失效，因為 Homebrew 的 cargo 不認得這些參數。解決方式是在 evaluate script 中使用 nightly toolchain 的絕對路徑（詳見第 4 節）。

---

## 2. 選擇優化目標

好的優化目標應滿足以下條件：

- **範圍小**：單一檔案或少數檔案（2-3 個），agent 才能有效理解與修改
- **可量測**：有現成的 benchmark 或容易撰寫
- **指標明確**：一個數字，方向清楚（越大越好或越小越好）

以 rapier/parry 為例：

- Parry 的 cylinder-cylinder 碰撞沒有專門的演算法，使用的是泛用的 GJK + PolygonalFeatureMap fallback 路徑
- 核心邏輯集中在 2 個檔案，約 150 行
- Parry 已有 `bench_cylinder_against_cylinder` benchmark，無需額外撰寫

---

## 3. 專案設置

```bash
# 建立專案目錄並 clone 目標 repo
mkdir -p ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
cd ~/Documents/Hack/crucible_projects/optimize-rapier-cylinder
git clone https://github.com/dimforge/parry.git .

# 建立 crucible 設定目錄
mkdir -p .crucible
```

接下來建立三個關鍵檔案：`.crucible/config.yaml`、`evaluate.sh`、`.crucible/program.md`。

---

## 4. 關鍵檔案

### .crucible/config.yaml

```yaml
name: "optimize-rapier-cylinder"

files:
  editable:
    - "src/shape/polygonal_feature_map.rs"
    - "src/query/contact_manifolds/contact_manifolds_pfm_pfm.rs"
  readonly:
    - "src/shape/cylinder.rs"
    - "src/query/gjk/gjk.rs"
    - "src/shape/polygonal_feature3d.rs"
    - "crates/parry3d/benches/query/contacts.rs"
    - ".crucible/program.md"
  hidden:
    - "evaluate.sh"

commands:
  run: "bash evaluate.sh 2>&1 | tee run.log"
  eval: "cat run.log"

metric:
  name: "ns_per_iter"
  direction: "minimize"

constraints:
  timeout_seconds: 300
  max_retries: 5
  plateau_threshold: 6

agent:
  instructions: "program.md"

git:
  branch_prefix: "crucible"
```

設定重點：

- `editable` 只列出 agent 可以修改的檔案，限制在碰撞偵測的核心路徑上
- `readonly` 讓 agent 能讀取相關的型別定義和 benchmark 程式碼，理解上下文
- `hidden` 隱藏 evaluate.sh，防止 agent 修改評估腳本來「作弊」
- `timeout_seconds: 300` 要足夠寬裕，涵蓋 Rust 增量編譯時間 + 測試時間 + benchmark 時間

### evaluate.sh

這是評估腳本，包含正確性閘門和效能量測。注意 Homebrew 的 workaround：

```bash
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Homebrew cargo/rustc shadows rustup — use nightly toolchain directly
NIGHTLY_RUSTC="$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/rustc"
NIGHTLY_CARGO="$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/cargo"
export RUSTC="$NIGHTLY_RUSTC"
export RUSTDOC="$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/rustdoc"

if [ ! -x "$NIGHTLY_CARGO" ]; then
    echo "ERROR: nightly cargo not found at $NIGHTLY_CARGO"
    echo "ns_per_iter: 999999999"
    exit 0
fi

# Phase 1: Correctness gate (skip doc-tests to avoid ABI mismatch)
echo "=== Running cylinder tests ==="
if ! "$NIGHTLY_CARGO" test -p parry3d --lib --tests -- cylinder 2>&1; then
    echo "TESTS FAILED — correctness broken"
    echo "ns_per_iter: 999999999"
    exit 0
fi

# Phase 2: Performance measurement
echo ""
echo "=== Running cylinder benchmark ==="
BENCH_OUTPUT=$("$NIGHTLY_CARGO" bench -p parry3d -- cylinder_against_cylinder 2>&1)
echo "$BENCH_OUTPUT"

# Parse nightly bench output: "bench:       3,802.78 ns/iter (+/- 313.41)"
NS_PER_ITER=$(echo "$BENCH_OUTPUT" | grep "cylinder_against_cylinder" | grep "ns/iter" | \
    sed 's/.*bench:[[:space:]]*//' | sed 's/[[:space:]]*ns\/iter.*//' | tr -d ',')

if [ -z "$NS_PER_ITER" ]; then
    echo "ERROR: Could not parse benchmark result"
    echo "ns_per_iter: 999999999"
    exit 0
fi

echo ""
echo "ns_per_iter: $NS_PER_ITER"
```

建立後記得設定執行權限：

```bash
chmod +x evaluate.sh
```

#### 三個實戰踩坑

**1. Homebrew cargo 覆蓋 rustup**

在 macOS 上，如果透過 Homebrew 安裝過 Rust，`/opt/homebrew/bin/cargo` 會出現在 PATH 前面，覆蓋 rustup 的 shim。這個 Homebrew 版的 cargo 完全忽略 `+nightly` 語法、`RUSTUP_TOOLCHAIN` 環境變數、以及 `rustup run nightly` wrapper。唯一可靠的解法是直接使用 nightly toolchain 的絕對路徑：

```bash
NIGHTLY_CARGO="$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/cargo"
export RUSTC="$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/rustc"
```

**2. Parry 使用 nightly `#[bench]`，不是 Criterion**

Parry 的 benchmark 使用 Rust nightly 內建的 `#[bench]` attribute，輸出格式為：

```
bench:       3,802.78 ns/iter (+/- 313.41)
```

而不是 Criterion 的 `time: [low median high]` 格式。evaluate.sh 的 parsing 邏輯必須對應正確的格式。如果你的目標專案使用 Criterion，需要調整 regex。

**3. Doc-test ABI 不相容**

`cargo test` 預設會執行 doc-tests，但 doc-tests 使用 PATH 上的 rustdoc（可能是 Homebrew 的 stable 版），而 library 是用 nightly 編譯的，導致 ABI mismatch 錯誤。解法是加上 `--lib --tests` 旗標跳過 doc-tests：

```bash
"$NIGHTLY_CARGO" test -p parry3d --lib --tests -- cylinder
```

### .crucible/program.md

```markdown
# Optimize Cylinder-Cylinder Collision Performance in Parry

## Goal
Reduce the execution time of `bench_cylinder_against_cylinder` by optimizing
the narrowphase contact manifold generation for cylinder-cylinder pairs.

## Background
Parry uses a generic GJK + PolygonalFeatureMap (PFM) path for cylinder-cylinder
collisions. There is no specialized cylinder-cylinder algorithm. The current
pipeline:
1. GJK finds closest points between two cylinders (via SupportMap trait)
2. PolygonalFeatureMap extracts approximate polygonal features
3. PolygonalFeature::contacts() clips features to generate contact manifold

## Editable Files
- `src/shape/polygonal_feature_map.rs`
- `src/query/contact_manifolds/contact_manifolds_pfm_pfm.rs`

## Reference Files (readonly)
- `src/shape/cylinder.rs`
- `src/query/gjk/gjk.rs`
- `src/shape/polygonal_feature3d.rs`

## Optimization Strategies to Consider
- Reduce allocations (SmallVec, stack arrays)
- Early-exit for parallel axes, coaxial, large separation
- Cache/precompute rotation/projection
- #[inline] on hot functions

## Hard Rules
1. DO NOT run scripts or cargo commands
2. DO NOT modify files outside editable list
3. DO NOT break public API
4. DO NOT weaken correctness
5. Read BOTH editable files FIRST
6. Make ONE focused optimization per iteration
```

設計考量：

- **「DO NOT run scripts or cargo commands」**：這條規則至關重要。如果不明確禁止，agent 會花整個 timeout 嘗試執行 `cargo build` 或 `cargo test` 來驗證修改，但 Crucible 的 agent 只有 Read/Edit/Write/Glob/Grep 五個工具，無法執行指令。明確禁止可以避免浪費迭代時間。
- **「Make ONE focused optimization per iteration」**：每次只做一個修改，讓 Crucible 的 keep/discard 機制能準確判斷哪個修改有效。複合修改中如果一個改善 5% 但另一個劣化 3%，淨效果 2% 會被保留，但你失去了單獨保留 5% 改善的機會。
- **READ-first workflow**：要求 agent 先讀取兩個 editable 檔案，確保 agent 理解現有程式碼再動手修改，而不是憑猜測編輯。
- **Suggested strategies**：提供具體的優化方向（allocation reduction、early-exit、precompute、`#[inline]`），引導 agent 走向有效的優化路徑，而不是嘗試不切實際的改動。

---

## 5. 正確性守護策略

Crucible 優化 Rust 專案時有多層正確性保護：

1. **Rust 編譯器**：型別錯誤、lifetime 問題、borrow checker 違規會在編譯階段被擋下，agent 的修改根本無法產生執行檔
2. **cargo test 閘門**：evaluate.sh 先執行現有的 cylinder 相關測試，確保 agent 的修改不會破壞正確性
3. **懲罰 metric（非 crash）**：測試失敗時輸出 `ns_per_iter: 999999999` 而不是讓 script crash。這讓 agent 收到「這個方向很差」的訊號，而不是一個含糊的錯誤訊息。Crucible 會將這個極大值與 baseline 比較後自動 discard
4. **API 鎖定**：function signature 定義在 readonly 檔案中，agent 無法修改公開介面

這個多層策略的好處是 agent 可以大膽嘗試——即使改壞了，correctness gate 會把它擋下，Crucible 會 discard 這次迭代並回滾到上一個好的版本。

---

## 6. 驗證與執行

```bash
# 首次完整建置（一次性，約 20-30 秒）
RUSTC="$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/rustc" \
  "$HOME/.rustup/toolchains/nightly-aarch64-apple-darwin/bin/cargo" \
  build -p parry3d --release

# 驗證 crucible 設定
crucible validate

# 開始自動優化（Ctrl+C 隨時停止）
crucible run --tag run-1 --no-interactive

# 查看結果
crucible history --tag run-1
```

首次建置很重要：Rust 的增量編譯依賴初始的 build artifacts。如果跳過這步，第一次迭代會包含完整編譯時間，可能超過 timeout。

---

## 7. 實際結果

以下是實際實驗的結果：

| Iter | ns/iter | Status | Improvement | Description |
|---|---|---|---|---|
| baseline | ~3800 | - | - | - |
| 1 | 3762.17 | keep | -1.0% | Zero-copy replace heap-allocating clone |
| 2 | 3736.17 | keep | -1.7% | Remove unused pos12.inverse() computation |
| 3 | 3725.93 | keep | -1.9% | Cone-related optimization |
| 4 | 3557.72 | keep | -6.4% | #[inline] on generic manifold function |
| 5 | 3581.32 | discard | - | Reuse pre-computed dist (didn't help) |
| 6 | 3642.64 | discard | - | #[inline(always)] (made it slower) |

觀察重點：

- **Agent 能理解並修改 Rust 程式碼**：包括 generic functions、trait implementations、lifetime annotations
- **最有效的優化是 `#[inline]`（iteration 4）**：在 generic function 上加上 `#[inline]` 讓編譯器能跨 crate 內聯，帶來最大的單次改善（從 -1.9% 跳到 -6.4%）
- **Agent 能從失敗中學習**：iteration 5 嘗試重用預計算的距離值但沒有改善，iteration 6 嘗試 `#[inline(always)]` 反而更慢——兩者都被正確 discard
- **總計：6.4% 提速，約 10 分鐘的自主優化時間**

---

## 8. 推廣到其他 Rust 專案

將 Crucible 應用到其他 Rust 專案的一般步驟：

1. **Clone 目標 repo 作為專案根目錄**：Crucible 在專案根目錄中運作，直接 clone 到專案目錄即可
2. **找出 hot path 檔案**：使用 profiler（`cargo flamegraph`、`perf`）或閱讀既有 benchmark 來定位效能瓶頸
3. **設定 editable 為這些檔案**：只開放需要優化的檔案，其餘皆為 readonly 或不列入
4. **撰寫 evaluate.sh**：`cargo test`（correctness gate）+ `cargo bench`（metric extraction）
5. **設定足夠的 timeout**：增量編譯時間 + 測試時間 + benchmark 時間 + 緩衝，通常 300-600 秒
6. **撰寫 program.md**：提供演算法背景、建議的優化方向、禁止執行腳本

### 注意事項

- **增量編譯時間是主要瓶頸**：每次迭代 Rust 需要 30-60 秒的增量編譯，相比 Python 專案的幾秒鐘。這限制了單位時間內能嘗試的迭代數
- **Agent 處理 borrow checker 錯誤的能力尚可**：大多數情況下能根據編譯錯誤訊息修正，但複雜的 lifetime 問題可能導致迭代浪費
- **容易獲得的優化**：`#[inline]`、allocation reduction（`Vec` 換 `SmallVec` 或 stack array）、移除 dead code——這些是 agent 最容易做到的改善
- **困難的優化**：SIMD intrinsics、重大演算法變更、unsafe 最佳化——這些通常超出 agent 在單次迭代中能完成的範圍
