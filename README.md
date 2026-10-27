# tea-activities

茶饮活动数据源仓库。App 从这里拉取真实活动数据：

- `data/activities.json` — 活动数据（App 拉取）
- `images/` — 活动官方海报（从资讯来源抓取）
- `scripts/update_activities.py` — 更新脚本
- `.github/workflows/update_activities.yml` — 手动更新任务（每日定时调用已停用）

## 工作流程

1. 仅由 GitHub Actions 手动触发，不再每日自动运行
2. 固定使用免费模式：抓取公开网页并用关键词规则提取
3. 不调用 DeepSeek、qwen 或其他付费模型
4. 更新 `data/activities.json` 并推送

## 所需 Secrets

在 Settings → Secrets and variables → Actions 中配置：

- `DASHSCOPE_API_KEY` — 阿里云百炼 key
