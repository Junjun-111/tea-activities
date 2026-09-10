# tea-activities

茶饮活动数据源仓库。App 从这里拉取真实活动数据：

- `data/activities.json` — 活动数据（App 拉取）
- `images/` — 活动官方海报（从资讯来源抓取）
- `scripts/update_activities.py` — 更新脚本
- `.github/workflows/update_activities.yml` — 定时任务（每天北京 0/6/12/18 点）

## 工作流程

1. GitHub Actions 定时触发
2. qwen-max 联网搜索最近 3 天茶饮行业最新资讯（限定时间窗口）
3. qwen-max 提取结构化活动信息（含官方海报直链）
4. 下载官方海报到 `images/`，更新 `data/activities.json` 并推送

## 所需 Secrets

在 Settings → Secrets and variables → Actions 中配置：

- `DASHSCOPE_API_KEY` — 阿里云百炼 key
