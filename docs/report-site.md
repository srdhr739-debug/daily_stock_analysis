# 手机报告网站（GitHub Pages）

在 fork 仓库中，每次「每日股票分析」工作流运行结束后，可以自动把生成的 `reports/*.md` 发布成一个适合手机浏览的静态网站，按日期累积历史报告。

- 工作流：`.github/workflows/report-pages.yml`（由 `workflow_run` 在「每日股票分析」完成后触发，也可手动运行）
- 生成脚本：`scripts/build_report_site.py`（仅依赖 `markdown2`）
- 发布位置：`gh-pages` 分支，网址一般为 `https://<用户名>.github.io/<仓库名>/`

## 启用步骤

1. 先按 [完整指南](full-guide.md) 让「每日股票分析」在 GitHub Actions 中正常运行（至少配置一个 AI Key，例如 `GEMINI_API_KEY`，以及 `STOCK_LIST`）。
2. `Settings → Secrets and variables → Actions → Variables`，新增 Repository variable：`REPORT_SITE_ENABLED=true`。
3. 手动运行一次「每日股票分析」（或等待定时任务），完成后「报告网站（GitHub Pages）」会自动运行并创建 `gh-pages` 分支。
4. `Settings → Pages → Build and deployment`：Source 选 `Deploy from a branch`，Branch 选 `gh-pages` / `/ (root)`，保存。
5. 几分钟后用手机打开 Pages 网址即可；可在浏览器中「添加到主屏幕」。

## 可选配置（Repository variables）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REPORT_SITE_ENABLED` | 未设置（关闭） | 设为 `true` 才会发布网站 |
| `REPORT_SITE_KEEP_DAYS` | `60` | 只保留最近 N 天的报告，`0` 表示全部保留 |

## 识别的报告文件

`report_YYYYMMDD.md`（汇总日报）、`report_YYYYMMDD_<代码>.md`（单股报告）、`market_review_YYYYMMDD.md`（大盘复盘）、`dashboard_YYYYMMDD.md`。其他 Markdown 文件会被忽略。报告中的原始 HTML 会被转义后显示。

## 注意

- 免费账户的 GitHub Pages 是**公开**的：知道网址的人都能看到报告（页面带 `noindex`，但这不是访问控制）。如需私密查看，请改用 Telegram 等[通知渠道](notifications.md)。
- 网站只展示已生成的报告，不会在浏览器端重新分析。
- 回滚：删除 `REPORT_SITE_ENABLED` 变量即可停止发布；在 Pages 设置中关闭站点并删除 `gh-pages` 分支即可下线。
