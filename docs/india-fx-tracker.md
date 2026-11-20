# India & Forex Daily 看板

一个免费、可选的看板：跟踪印度股市（NIFTY 50、SENSEX、NIFTY BANK 与自选 NSE/BSE 个股）和外汇（USD/INR、EUR/INR 等）。交易时段每 15 分钟刷新一次行情卡片，顶部嵌入 TradingView 实时滚动行情；收盘后生成当日报告，并按日期保留「Daily activity」历史报告。

- 代码：`src/india_fx_tracker.py`（日报：`python -m src.india_fx_tracker --site site`；盘中刷新：加 `--intraday`）
- 工作流：`.github/workflows/india-fx-daily.yml`
  - `intraday`：工作日 IST 09:15–15:45 每 15 分钟，只刷新看板（规则摘要，不调用 AI，不写日报，仅安装 `yfinance`/`markdown2`）；行情与上次完全相同时不改动网站
  - `daily`：工作日 IST 16:50（UTC 11:20），生成当日报告与 AI 简报
  - 手动运行可选择 `daily` / `intraday`
- 发布位置：`gh-pages` 分支的 `india/` 目录，网址一般为 `https://<用户名>.github.io/<仓库名>/india/`
- 测试：`tests/test_india_fx_tracker.py`（离线）

## 内容

- **今日简报**：配置了任一 AI Key（如免费的 `GEMINI_API_KEY`）时，复用本项目 `GeminiAnalyzer` 的 LLM 配置生成简报，可用 `INDIA_FX_REPORT_LANGUAGE=ta` 输出泰米尔语；未配置或调用失败时自动降级为规则摘要（市场情绪、涨跌最大个股、卢比走势、需关注信号）。
- **行情卡片**：收盘价、日涨跌、5 日 / 1 个月涨跌、RSI14、MA20/MA50 趋势、近 30 日走势线，以及 RSI 超买超卖、穿越 MA20、6 个月新高/新低、大幅波动等提示。
- **实时部分**：页面顶部为 TradingView 免费滚动行情组件（浏览器端实时加载）；页面每 5 分钟自动重新加载，以显示最新一次刷新结果。
- **Daily activity**：每个交易日一份 Markdown 报告（同时生成 HTML 页面），`india/data/*.json` 保存原始指标，`india/data/latest.json` 为最新数据。

## 启用步骤（GitHub Actions + Pages，均免费）

1. `Settings → Secrets and variables → Actions → Variables` 新增 `INDIA_FX_ENABLED=true`。
2. （可选）`Secrets` 中新增 `GEMINI_API_KEY`（Google AI Studio 免费申请），启用 AI 简报。
3. `Actions → India & Forex Daily → Run workflow` 手动运行一次，生成 `gh-pages` 分支。
4. `Settings → Pages`：Source 选 `Deploy from a branch`，Branch 选 `gh-pages` / `/ (root)`。
5. 用手机打开 `https://<用户名>.github.io/<仓库名>/india/`，可「添加到主屏幕」。

与 [手机报告网站](report-site.md) 共用 `gh-pages` 分支，两个工作流共享并发组 `gh-pages-publish`，不会同时推送。

## 配置（Repository variables 或 `.env`）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `INDIA_FX_ENABLED` | 未设置（关闭） | 仅 GitHub Actions：设为 `true` 才运行 |
| `INDIA_FX_INDICES` | `^NSEI:NIFTY 50,^BSESN:SENSEX,^NSEBANK:NIFTY BANK` | Yahoo 指数代码，可写 `代码:显示名` |
| `INDIA_FX_STOCKS` | `RELIANCE.NS,TCS.NS,HDFCBANK.NS,INFY.NS,ICICIBANK.NS` | NSE 用 `.NS`，BSE 用 `.BO` |
| `INDIA_FX_PAIRS` | `USDINR=X,EURINR=X,GBPINR=X,JPYINR=X,EURUSD=X` | Yahoo 外汇代码 |
| `INDIA_FX_AI_ENABLED` | `true` | 设为 `false` 始终使用规则摘要 |
| `INDIA_FX_REPORT_LANGUAGE` | `en` | AI 简报语言，如 `ta`、`hi`；规则摘要与表格为英文 |
| `INDIA_FX_KEEP_DAYS` | `90` | 保留最近 N 天报告，`0` 全部保留 |
| `INDIA_FX_LIVE_ENABLED` | `true` | 仅 GitHub Actions：设为 `false` 关闭每 15 分钟的盘中刷新，只保留收盘日报 |
| `INDIA_FX_LIVE_SYMBOLS` | `BSE:SENSEX:SENSEX,NSE:NIFTY:NIFTY 50,FX_IDC:USDINR:USD/INR,...` | TradingView 代码 `交易所:代码[:显示名]`；`off` 关闭滚动行情 |

## 边界与说明

- 「实时」的边界：卡片数据最快约 15 分钟更新一次（GitHub 定时任务在高峰期可能再延迟 5–15 分钟，Yahoo 行情本身也可能延迟）；只有顶部 TradingView 滚动条是浏览器端实时数据。部分交易所代码（如 NSE/BSE）可能在免费组件中不可用，此时可在 `INDIA_FX_LIVE_SYMBOLS` 中移除或替换。
- 盘中刷新同一天合并为一个 `gh-pages` 提交（amend + `--force-with-lease`），避免分支历史无限增长；日报提交保持独立。
- 公共仓库 60 天无活动时 GitHub 会自动停用定时任务，需要在 Actions 页面重新启用。
- 数据来自 Yahoo Finance，可能延迟或缺失；单个代码失败只显示「Data unavailable」，不影响其他代码；全部失败时不更新网站并以非零状态退出。
- 报告日期取行情数据的最后交易日；同一交易日已生成过时自动跳过（节省 AI 额度），`--force` 或手动运行勾选 `force` 可重建。
- 此看板独立于主分析流程，不改变 `main.py`、A 股/港股/美股分析、通知或 Web/API 行为。
- GitHub Pages 免费版为**公开**页面。仅供参考，不构成投资建议。
- 回滚：设 `INDIA_FX_LIVE_ENABLED=false` 只停止盘中刷新；删除 `INDIA_FX_ENABLED` 变量即停止全部更新；删除 `gh-pages` 分支中的 `india/` 目录即可下线。
