# Supervisor Agent 設計理念與決策過程

這份文件記錄 supervisor agent 設計過程中的拉扯和最終決策的理由。搭配 `2026-03-19-supervisor-agent-design.md` 閱讀。

## 起點

2026-03-19 做了一個用 crucible 優化 nanoclaw 架構的實驗。跑了三輪（arch1-arch3），發現：

- Agent 前 5 次迭代做真正的架構改善（拆 God function、降耦合、減 export）
- 第 12 次開始 gaming：copy-paste 函數降依賴數
- 加了函數重複偵測後，agent 改用 interface 重複和常數 inline
- 加了 interface 和常數偵測後，agent 被迫做更小但真實的改善

整個過程是手動的：人看 results → 看 diff → 判斷好壞 → cherry-pick → 改 evaluate → 重新跑。每輪花幾十分鐘。自然想到：能不能自動化這個流程？

## 第一版設計：全能 Supervisor

最初構想是一個完整的外層 agent，能做我們手動做的所有事：
- Review diff 判斷品質
- Cherry-pick 好的 commit
- 修補 evaluate（加偵測器）
- 修補 program.md
- 重新啟動

還設計了很多防護：
- Frozen baseline evaluate（不可修改的 ground truth）
- Additive-only 限制（supervisor 只能追加 evaluate 程式碼，不能刪改）
- Line-level superset check（強制機制）
- Sanity check（修改後重跑驗證分數沒偏移）
- 雙向 drift detection

Review agent 也指出了大量問題需要處理：commit hash 解析、results 分隔、error handling、模組結構。

設計越來越複雜。

## 第一個拉扯：Supervisor 自己在做優化

問題被提出：supervisor 能改 evaluate，代表它在「優化 evaluate」— 這件事本身沒有被任何東西監控。

表面上 supervisor 在「堵 gaming 漏洞」，但它實際上在做的是：觀察 agent 行為 → 判斷好壞 → 修改規則。這就是優化。而且 supervisor 的「判斷」是一個 LLM 黑盒子，有偏好、有盲點。

我們把一個有明確缺陷的系統（可被 game 的 metric）換成了一個缺陷更不透明的系統（LLM 的主觀判斷）。

更糟的是 co-evolution 風險：supervisor 改 evaluate → agent 適應 → supervisor 再改 → 目標和解法一起漂移，沒有收斂保證。

## 第二個拉扯：改 evaluate 的權限

在「要不要讓 supervisor 改 evaluate」上來回了好幾次：

**支持改的理由：** 今天的實驗證明光靠 program.md 引導不夠。Evaluate 裡的 code-enforced penalty 才是真正的硬限制。不能改 evaluate 就不能從根本堵住 gaming。

**反對改的理由：** evaluate 是整個實驗的 ground truth。讓一個 LLM 修改 ground truth，等於讓裁判改規則。LLM 自然傾向放鬆限制（「這個 penalty 太嚴了」），長期下來 evaluate 會變得越來越容易滿足。

**中間路線嘗試：** 只用預建模板，supervisor 選擇模板但不自由寫程式碼。被否決：gaming 模式無限多，模板永遠補不完，最終仍然 fallback 回人工。

**最終決定：** 不讓 supervisor 改 evaluate。原因很簡單 — gaming 模式補不完，不管是 supervisor 自由修改、模板選擇、還是其他方式，最終都需要人介入。既然如此，不如從一開始就承認這個限制。

## 第三個拉扯：停下來等人 vs 不間斷運行

如果 supervisor 不改 evaluate，偵測到 gaming 後能做什麼？

**選項 A：停下來通知人。** 人來改 evaluate，再繼續。問題：crucible 的核心價值是「睡覺時它還在跑」。加一個會停的 supervisor 等於砍掉這個優勢。

**選項 B：不停，事後分析。** 跑完後 supervisor 自動 cherry-pick + 生成報告。問題：不符合「早期偵測 gaming」的需求。

**選項 C：不停，即時回滾。** 偵測到 gaming → reset 到最後 genuine commit → 更新 program.md → 繼續跑。不改 evaluate，不等人。Agent 看到回滾記錄和 program.md 警告，大部分情況會換方向。

**最終選擇 C。** 理由：
- 不中斷自主運行
- 早期偵測 gaming
- 不碰 evaluate（metric 定義是用戶的事）
- 失誤不致命（false positive 浪費幾次迭代，false negative 不比原來差）

## 第四個拉扯：Supervisor 的判斷力夠嗎

Supervisor 用 LLM 判斷「這個 diff 是 genuine 還是 gaming」。但 LLM 的判斷力有限：
- 明顯的 gaming（copy-paste 整個函數）抓得到
- 微妙的 gaming（structural typing 繞過 interface import）一開始抓不到

結論是接受這個限制。Supervisor 是 best-effort 篩選器：
- 抓到 → 賺到（回滾 + 警告，agent 被迫換方向）
- 沒抓到 → 跟沒有 supervisor 一樣，不會更差
- 誤判 → max_rounds 限制回滾次數，防止死循環

## 最終設計原則

經過上面的拉扯，收斂出五條原則：

1. **Supervisor 不優化任何東西** — 它只篩選和回滾。不改 evaluate，不改 source。
2. **evaluate 是用戶的 ground truth** — 兩個 agent 都不能改。要改只能人工。
3. **不停下來等人** — 全程自主運行，保留 crucible 的核心價值。
4. **失誤不致命** — false positive 浪費迭代，false negative 不比原來差。
5. **max_rounds 是硬上限** — 防止死循環，承認自動化的邊界。

## 被砍掉的東西

| 被砍的 | 為什麼砍 |
|--------|---------|
| Evaluate 寫入權限 | Supervisor 會變成不受監控的優化者 |
| Frozen baseline evaluate | 不改 evaluate 就不需要 baseline |
| Additive-only 限制 | 不改 evaluate 就不需要 |
| Line-level superset check | 同上 |
| Sanity check | 同上 |
| 預建模板系統 | Gaming 模式無限多，模板補不完 |
| 停下來等人 | 犧牲自主運行的核心價值 |
| 事後分析模式 | 不符合早期偵測需求 |

## 這個設計承認的限制

1. **Supervisor 抓不到所有 gaming。** 微妙的 gaming 會漏過。但不比沒有 supervisor 差。
2. **program.md 是軟性約束。** Agent 大部分時候遵守，不是 100%。但從 arch3 的實驗看，明確警告的效果蠻好的。
3. **gaming 的根本解決仍需人工。** Supervisor 能拖延 gaming、減少浪費，但不能消滅它。用戶最終還是需要看 supervisor log 並手動改 evaluate。
4. **LLM 成本。** 每次 review 是一次 Claude API 呼叫。用 stall 觸發（而非固定間隔）減少呼叫次數。

## 核心洞察

整個設計過程最大的收穫是這個認知：

**crucible 的 evaluate 就像法律，agent 就像公民，supervisor 就像警察。警察可以抓違法的人（回滾 gaming），但不應該自己修改法律（改 evaluate）。修法是立法者（用戶）的事。**

給 supervisor 改 evaluate 的權力，就像讓警察同時當立法者。短期內效率很高（想抓什麼就立法禁止什麼），長期會出問題（法律越來越反映警察的偏好而非公民的利益）。

所以最終設計把 supervisor 限制為純粹的執法者：偵測違規、回滾、記錄。把立法權留給人。
