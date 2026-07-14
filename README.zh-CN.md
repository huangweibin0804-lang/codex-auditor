# Codex-优化审查小助手

本地优先的 Codex 工作复盘 Skill。审查会话、项目、返工模式和下周期行动，并生成可验证的本地报告。

## 最简单的安装方式：把链接发给 Codex

在 Codex 里新建一个任务，把下面整段发出去：

```text
请安装这个 Codex Skill，并将它放入我的本地 Skills 目录：
https://github.com/huangweibin0804-lang/Codex-Optimization-Audit-Assistant
```

这适用于支持从 GitHub 读取并写入本地 Skills 的 Codex 客户端。若客户端要求确认目录或权限，按提示确认即可。

安装完成后发送：

```text
用 Codex-优化审查小助手审查我最近 7 天的使用记录
```

## 这个 Skill 做什么

- 汇总 1、7、30 天或自定义周期内的 Codex 使用情况
- 区分可观察事实、确定性活动估算、规则型发现和行动建议
- 识别项目、返工、约束追加、失败和范围改向模式
- 用统一入口自动推进项目分类、深度分析、校验和失败降级
- 报告第一屏先展示一句话结论、主要投入、最大卡点和三项行动
- 生成本地 HTML、Markdown、JSON、证据 manifest，以及可选 PDF
- 富分析只使用经过筛选和脱敏的摘要，并保留证据与版本信息

## 隐私边界

- 原始 Codex JSONL、完整工具输出、完整文件内容、凭据和联系人信息不进入富分析输入
- 确定性扫描、脱敏、manifest 和报告渲染在本机完成
- 富分析摘要会进入当前 Codex 模型链路；只做本地统计时发送“只做本地统计”
- 报告中的 interaction activity estimate 不代表真实工时或项目完成度

## 手动安装

```bash
git clone https://github.com/huangweibin0804-lang/Codex-Optimization-Audit-Assistant.git /tmp/codex-auditor-repo
python3 /tmp/codex-auditor-repo/codex-auditor/scripts/install_skill.py install
```

默认使用中文入口。需要英文版本时，将 `SKILL.en.md` 复制为安装目录中的 `SKILL.md`。

## 本地运行与测试

```bash
cd codex-auditor
python3 scripts/run_audit.py start --mode local --days 7
python3 scripts/test_auditor.py
python3 scripts/test_rich_audit.py
python3 scripts/test_workflow.py
python3 scripts/validate_skill_security.py .
```

检查 GitHub 源码与本机安装版是否一致：

```bash
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
```

## 报告阅读顺序

新版报告第一屏是一张“审查结论牌”：一句话结论、主要投入、最大卡点和富分析状态。紧接着展示按优先级排序的三项行动。数据概览和项目复盘随后出现，活动趋势、小时图与场景分布收进可展开区域。

PDF 是可选步骤，需要本机有 Playwright：

```bash
npm install playwright
node scripts/render_report_pdf.js ~/.codex/reports/<report-id>.html ~/.codex/reports/<report-id>.pdf
```

## 当前状态

这是公开预览版。确定性审计、预算受限的富分析流程、本地证据处理和自动化测试已包含；外部用户重复性与更广泛环境兼容性仍属于发布闸门。

许可证尚未指定。需要修改、再发布或商业使用时，请先与作者确认授权范围。
