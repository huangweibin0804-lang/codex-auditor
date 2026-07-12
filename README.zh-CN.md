# Codex 使用审计器

本地优先的 Codex 使用复盘 Skill。它从本机 Codex 会话日志生成 Markdown、HTML、JSON 和证据 manifest，区分可观察事实、确定性活动估算、规则型发现和后续行动。

## 安装

```bash
git clone https://github.com/huangweibin0804-lang/codex-auditor.git /tmp/codex-auditor-repo
mkdir -p ~/.codex/skills
cp -R /tmp/codex-auditor-repo/codex-auditor ~/.codex/skills/codex-auditor
```

默认使用中文 Skill。需要英文版本时，将 `codex-auditor/SKILL.en.md` 复制或重命名为目标目录中的 `SKILL.md`。

安装后，在 Codex 中发送：

```text
用 Codex审查器审查我最近 7 天的使用记录
```

也可以指定 1、7、30 天，或不超过 90 天的自定义日期范围。

## 本地运行与测试

```bash
cd codex-auditor
python3 scripts/audit_codex_usage.py --days 7
python3 scripts/test_auditor.py
python3 scripts/test_rich_audit.py
python3 scripts/validate_skill_security.py .
```

报告默认写入 `~/.codex/reports/`。PDF 是可选步骤，需要本机有 Playwright：

```bash
npm install playwright
node scripts/render_report_pdf.js ~/.codex/reports/<report-id>.html ~/.codex/reports/<report-id>.pdf
```

## 隐私边界

- 确定性统计、日志扫描、脱敏、manifest 和报告渲染在本机完成。
- Skill 不把原始 JSONL 上传到 Skill 作者或分析服务。
- 富分析模式会把经过筛选和脱敏的历史摘要发送到当前 Codex 模型链路；需要完全本地处理时，使用“只做本地统计”。
- 报告使用 Codex interaction activity estimate 这一口径，不代表真实工时、项目完成度或完整的任务边界。

## 目录

```text
codex-auditor/
├── SKILL.md                 # 中文 Skill 入口
├── SKILL.en.md              # English Skill translation
├── agents/openai.yaml       # Codex agent metadata
├── references/              # 指标、输入输出契约与视觉规范
└── scripts/                 # 审计、富分析、验证器和测试
```

完整产品与验收规格见 [MVP-SPEC.md](MVP-SPEC.md)，英文公开摘要见 [MVP-SPEC.en.md](MVP-SPEC.en.md)。

## 当前状态

这是公开预览版。确定性审计、预算受限的富分析流程、本地证据处理和回归测试已包含；外部用户重复性与更广泛环境兼容性仍属于发布闸门。
