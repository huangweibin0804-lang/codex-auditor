# Codex 使用审计器

一个本地优先的 Codex 使用复盘 Skill。它从本机 Codex 会话日志生成 Markdown、HTML、JSON 和证据 manifest，区分可观察事实、确定性活动估算、规则型发现和后续行动。

## 使用方式

将 `codex-auditor/` 复制到 Codex 的 Skills 目录，或在支持从 GitHub 加载 Skill 的客户端中指向这个目录，然后发送：

```text
用 Codex审查器审查我最近 7 天的使用记录
```

也可以明确指定 1、7、30 天或不超过 90 天的自定义日期范围。

## 本地运行

```bash
cd codex-auditor
python3 scripts/audit_codex_usage.py --days 7
python3 scripts/test_auditor.py
python3 scripts/test_rich_audit.py
python3 scripts/validate_skill_security.py .
```

报告默认写入 `~/.codex/reports/`。PDF 是可选渲染步骤，需要本机有 Playwright：

```bash
npm install playwright
node scripts/render_report_pdf.js ~/.codex/reports/<report-id>.html ~/.codex/reports/<report-id>.pdf
```

## 隐私边界

- 确定性统计、日志扫描、脱敏、manifest 和报告渲染在本机完成。
- Skill 不把原始 JSONL 上传到 Skill 作者或分析服务。
- 富分析模式会把经过筛选和脱敏的历史摘要发送到当前 Codex 模型链路；需要完全本地处理时，使用“只做本地统计”。
- 报告用于观察 Codex interaction activity estimate，不代表真实工时、项目完成度或完整的任务边界。

## 目录

- `codex-auditor/SKILL.md`：Skill 入口和运行规范
- `codex-auditor/scripts/`：审计器、富分析流程、验证器和测试
- `codex-auditor/references/`：指标、输入输出 Schema 和报告规范
- `MVP-SPEC.md`：产品与验收规格
