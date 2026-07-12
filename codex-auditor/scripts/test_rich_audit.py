#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import rich_audit as rich


def row(timestamp, kind, payload):
    return json.dumps({"timestamp": timestamp, "type": kind, "payload": payload}, ensure_ascii=False)


def make_session(root: Path, session_id: str, title: str, day: int, workspace: Path, requests: list[str]) -> None:
    source = root / "sessions" / f"{session_id}.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        row(f"2026-07-{day:02d}T00:00:00Z", "session_meta", {"id": session_id, "cwd": str(workspace)}),
        row(f"2026-07-{day:02d}T00:00:01Z", "turn_context", {"turn_id": f"t-{session_id}", "cwd": str(workspace), "workspace_roots": [str(workspace)]}),
    ]
    minute = 1
    for request in requests:
        lines.append(row(f"2026-07-{day:02d}T00:{minute:02d}:00Z", "event_msg", {"type": "user_message", "message": request}))
        lines.append(row(f"2026-07-{day:02d}T00:{minute:02d}:30Z", "event_msg", {"type": "agent_message", "message": f"已处理：{request}"}))
        minute += 2
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with (root / "session_index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"id": session_id, "thread_name": title}, ensure_ascii=False) + "\n")


def main() -> None:
    start_7, end_7 = rich.parse_range(7, None, None)
    assert (end_7.date() - start_7.date()).days == 6 and start_7.time() == dt.time.min
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        root, output, workspace = base / "codex", base / "reports", base / "workspace"
        root.mkdir()
        workspace.mkdir()
        (workspace / "report.md").write_text("private content is never read", encoding="utf-8")
        make_session(root, "s1", "Skill 设计", 1, workspace, ["<appshot>noise</appshot>\n## My request for Codex:\n设计 Codex 审查 Skill 的目标和验收", "补充：必须本地运行，凭据 fdOAYPESkaBZne18muSiVcErBVUQfioT", "改为输出 HTML 报告"])
        make_session(root, "s2", "Skill 实现", 3, workspace, ["实现审计脚本并测试", "补充输出 manifest", "验证隐私权限"])
        make_session(root, "s3", "内容工作", 5, workspace, ["整理视频脚本文案", "改写开头", "归档到文档"])
        start = dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)
        end = dt.datetime(2026, 7, 7, tzinfo=dt.timezone.utc)
        prepared = rich.prepare(root, output, start, end, "test-model")
        state_path = Path(prepared["state"])
        state = rich.read_json(state_path)
        assert state["coverage"]["total_threads"] == 3
        assert len(prepared["cluster_inputs"]) == 1
        cluster_packet = rich.read_json(Path(prepared["cluster_inputs"][0]))
        assert rich.json_chars(cluster_packet) <= rich.MAX_BATCH_CHARS
        serialized_packet = json.dumps(cluster_packet, ensure_ascii=False)
        assert "<appshot>" not in serialized_packet and "fdOAYPESkaBZne18muSiVcErBVUQfioT" not in serialized_packet
        digests = {item["thread_ref"]: item for item in cluster_packet["thread_digests"]}
        refs = list(digests)
        project_output = {
            "stage": "project_clustering",
            "batch_id": "batch-01",
            "model_id": "test-model",
            "projects": [
                {"project_id": "p_01", "name": "Codex 审查器", "summary": "设计并实现本地审计 Skill", "confidence": "HIGH", "member_thread_refs": refs[:2], "evidence_ids": [digests[refs[0]]["thread_evidence_ids"][0], digests[refs[1]]["thread_evidence_ids"][0]]},
                {"project_id": "p_02", "name": "内容整理", "summary": "脚本改写与归档", "confidence": "MEDIUM", "member_thread_refs": refs[2:], "evidence_ids": [digests[refs[2]]["thread_evidence_ids"][0]]},
            ],
            "semantic_unclassified": [],
        }
        project_path = output / "projects.json"
        rich.write_private(project_path, project_output)
        deep_path = rich.prepare_deep(state_path, project_path)
        deep_packet = rich.read_json(deep_path)
        assert all(project["input_turn_count"] <= 30 for project in deep_packet["projects"])
        assert all(any("final_outcome" in turn["selection_buckets"] for turn in project["turns"]) for project in deep_packet["projects"])
        assert all("relative_path" not in json.dumps(project["workspace_scan"]) for project in deep_packet["projects"])
        evidence_by_project = {}
        for project in deep_packet["projects"]:
            evidence_by_project[project["project_id"]] = [turn["user_evidence_id"] for turn in project["turns"]]
        all_ids = [value for values in evidence_by_project.values() for value in values]
        deep_output = {
            "metadata": state["metadata"],
            "core_judgment": {"text": "审计器项目投入最高，且用户持续补充隐私和证据要求。", "evidence_ids": all_ids[:2], "confidence": "HIGH"},
            "projects": [],
            "work_patterns": {"problems": [{"text": "复杂任务中存在后续约束追加。", "evidence_ids": all_ids[:2], "confidence": "MEDIUM"}], "progress": [], "stable_strengths": [], "reusable_workflows": []},
            "actions": [{"behavior_change": "复杂任务开始时先写验收条件。", "trigger_condition": "开始 Skill 或产品实现时", "next_review_signal": "约束追加证据减少", "supporting_evidence_ids": all_ids[:2]}],
            "coverage": deep_packet["coverage"],
        }
        for project in deep_packet["projects"]:
            ids = evidence_by_project[project["project_id"]]
            deep_output["projects"].append({
                "project_id": project["project_id"], "name": project["name"], "confidence": project["confidence"],
                "claims": [
                    {"category": "work", "type": "fact", "text": "会话围绕该项目持续推进。", "evidence_ids": ids[:1], "confidence": "HIGH"},
                    {"category": "next_step", "type": "recommendation", "text": "继续用固定验收条件验证。", "evidence_ids": ids[:1], "confidence": "MEDIUM"},
                ],
            })
        deep_output_path = output / "deep-output.json"
        rich.write_private(deep_output_path, deep_output)
        assert rich.validate_deep_output(deep_output, state, set(evidence_by_project)) == []
        merged = rich.merge(state_path, project_path, deep_output_path)
        assert project_path.stat().st_mode & 0o777 == 0o600
        assert deep_output_path.stat().st_mode & 0o777 == 0o600
        html = Path(merged["html"]).read_text(encoding="utf-8")
        assert "项目版图" in html and "主要项目复盘" in html and "工作模式" in html
        assert all(label in html for label in ("做了什么", "卡点是什么", "优化方案"))
        assert "这说明什么" not in html and "下次可以怎么做" not in html
        assert "FACT" not in html and "INFERENCE" not in html and "RECOMMENDATION" not in html
        assert "Codex 审查器" in html
        assert str(workspace) not in html
        invalid = json.loads(json.dumps(deep_output))
        invalid["actions"][0]["supporting_evidence_ids"] = ["ev_unknown"]
        assert any("unknown evidence" in error for error in rich.validate_deep_output(invalid, state, set(evidence_by_project)))
    print("rich audit tests passed")


if __name__ == "__main__":
    main()
