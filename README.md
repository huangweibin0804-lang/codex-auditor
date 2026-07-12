# Codex 使用审计器 / Codex Usage Auditor

本地优先的 Codex 工作复盘 Skill。审查会话、项目、返工模式和下周期行动，并生成可验证的本地报告。

Local-first Codex workflow review Skill for sessions, projects, rework patterns, and next-cycle actions.

[![CI](https://github.com/huangweibin0804-lang/codex-auditor/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/huangweibin0804-lang/codex-auditor/actions/workflows/test.yml) · [中文文档](README.zh-CN.md) · [English](README.en.md)

## 最简单的安装方式：把 GitHub 链接发给 Codex

在 Codex 里新建一个任务，把下面整段发出去：

```text
请安装这个 Codex Skill，并将它放入我的本地 Skills 目录：
https://github.com/huangweibin0804-lang/codex-auditor
```

Codex 会读取仓库并按当前环境执行安装；如果当前客户端要求确认目录或权限，按提示确认即可。安装完成后，可以直接发送：

```text
用 Codex审查器审查我最近 7 天的使用记录
```

## 这个 Skill 做什么

- 汇总 1、7、30 天或自定义周期内的 Codex 使用情况
- 区分可观察事实、确定性活动估算、规则型发现和行动建议
- 识别项目、返工、约束追加、失败和范围改向模式
- 生成本地 HTML、Markdown、JSON、证据 manifest，以及可选 PDF
- 富分析只使用经过筛选和脱敏的摘要，并保留证据与版本信息

## 隐私边界

- 原始 Codex JSONL、完整工具输出、完整文件内容、凭据和联系人信息不进入富分析输入
- 确定性扫描、脱敏、manifest 和报告渲染在本机完成
- 富分析摘要会进入当前 Codex 模型链路；只做本地统计时发送“只做本地统计”
- 报告中的 interaction activity estimate 不代表真实工时或项目完成度

## 手动安装

当当前客户端无法自动安装时：

```bash
git clone https://github.com/huangweibin0804-lang/codex-auditor.git /tmp/codex-auditor-repo
mkdir -p ~/.codex/skills
cp -R /tmp/codex-auditor-repo/codex-auditor ~/.codex/skills/codex-auditor
```

默认使用中文入口。需要英文版本时，将 `SKILL.en.md` 复制为安装目录中的 `SKILL.md`。

## English

See [README.en.md](README.en.md) for the English quick-install prompt, manual setup, features, privacy boundary, and validation commands.

## 当前状态

这是公开预览版。确定性审计、预算受限的富分析流程、本地证据处理和自动化测试已包含；外部用户重复性与更广泛环境兼容性仍属于发布闸门。

## License

许可证尚未指定。需要修改、再发布或商业使用时，请先与作者确认授权范围。
