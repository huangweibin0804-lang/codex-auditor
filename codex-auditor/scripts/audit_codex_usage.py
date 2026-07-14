#!/usr/bin/env python3
"""Offline Codex JSONL auditor. Uses only the Python standard library."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
import unicodedata

ADAPTER_VERSION = "codex_jsonl_adapter_v1"
CHAIN_VERSION = "user_message_chain_v2"
SCENARIO_CLASSIFIER_VERSION = "scenario_classifier_v1"
ACTION_RULES_VERSION = "action_rules_v1"
SEMANTIC_INPUT_SCHEMA_VERSION = "semantic_audit_input_v2"
SEMANTIC_OUTPUT_SCHEMA_VERSION = "semantic_audit_output_v1"
ANALYSIS_PROMPT_VERSION = "rich_audit_prompt_v1"
UTC = dt.timezone.utc
LOCAL_TZ = dt.datetime.now().astimezone().tzinfo or UTC
STRONG_MARKERS = ("新任务", "另一个任务", "换个任务", "换个话题", "现在开始新")
WEAK_MARKER = "接下来做"
CONSTRAINT_MARKERS = ("补充", "必须", "禁止", "不要", "范围", "限制", "验收", "输出格式")
REDIRECT_MARKERS = ("改成", "改为", "换成", "重做", "先别", "停止", "不对")
SECRET_RE = re.compile(r"(?i)\b(?:sk|api[_-]?key|token|secret|password)[_-]?[a-z0-9]{8,}\b")
SENSITIVE_ID_RE = re.compile(r"\b(?:oc|ou|on|cli)_[A-Za-z0-9]{12,}\b")
HIGH_ENTROPY_RE = re.compile(r"\b(?=[A-Za-z0-9]{24,}\b)(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{24,}\b")
EMAIL_RE = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1\d{10}(?!\d)")
PATH_RE = re.compile(r"(?:(?:[A-Za-z]:)?/[^\s'\"`<>]{2,}|~/(?:[^\s'\"`<>]+))")
KNOWN_IGNORED_TOP_TYPES = {"session_meta", "turn_context", "world_state", "compacted", "inter_agent_communication_metadata", "response.done"}
KNOWN_IGNORED_EVENT_TYPES = {"token_count", "agent_reasoning", "task_started", "web_search_end", "thread_settings_applied", "turn_aborted", "context_compacted", "sub_agent_activity", "thread_rolled_back", "item_completed", "thread_goal_updated"}
KNOWN_IGNORED_RESPONSE_TYPES = {"reasoning", "function_call", "custom_tool_call", "web_search_call", "image_generation_call", "tool_search_call", "tool_search_output", "agent_message"}


@dataclass
class Event:
    kind: str
    timestamp: dt.datetime | None
    session_id: str
    workspace: str
    source_path: Path
    relative_source_path: str
    line_number: int
    line_bytes: bytes
    text: str = ""
    status: str = ""
    paths: tuple[str, ...] = ()
    chain_id: str = ""


def sha(value: bytes | str, length: int | None = None) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    value = hashlib.sha256(raw).hexdigest()
    return value[:length] if length else value


def parse_iso(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        return None


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u200b", "")
    return " ".join(value.split()).strip()


def clean_user_message(value: str) -> str:
    """Remove injected app/file context when an explicit user-request marker exists."""
    for marker in ("## My request for Codex:", "## My request for Codex：", "My request for Codex:"):
        if marker in value:
            value = value.rsplit(marker, 1)[1]
            break
    return value.strip()


def redact(value: str, limit: int = 240) -> str:
    value = SENSITIVE_ID_RE.sub("[敏感标识]", value)
    value = HIGH_ENTROPY_RE.sub("[高熵凭据]", value)
    value = PATH_RE.sub("[路径]", value)
    value = EMAIL_RE.sub("[邮箱]", value)
    value = PHONE_RE.sub("[电话]", value)
    value = SECRET_RE.sub("[敏感凭据]", value)
    return value[:limit]


def marker_at_start(text: str, marker: str) -> bool:
    text = normalize(text)
    if not text.startswith(marker):
        return False
    return len(text) == len(marker) or text[len(marker)] in " ：:,，。"


def bounded_task_complete_time(payload: dict[str, Any], fallback: dt.datetime | None) -> tuple[dt.datetime | None, bool]:
    value = payload.get("completed_at")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 946684800 <= value <= 4102444800:
        return dt.datetime.fromtimestamp(value, tz=UTC), True
    return fallback, fallback is not None


def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def structured_paths(value: Any, key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            found.extend(structured_paths(v, k))
    elif isinstance(value, list):
        for item in value:
            found.extend(structured_paths(item, key))
    elif key.lower() in {"path", "file_path", "output_path", "saved_path"} and isinstance(value, str):
        found.append(value)
    return found


def event_from_line(obj: dict[str, Any], source: Path, relative: str, line_number: int, raw: bytes, session_id: str, cwd: str) -> Event | None:
    top_type, payload = obj.get("type"), obj.get("payload")
    if not isinstance(payload, dict):
        return None
    stamp = parse_iso(obj.get("timestamp"))
    kind = ""
    text = ""
    status = str(payload.get("status", ""))
    if top_type == "event_msg":
        ptype = payload.get("type")
        if ptype == "user_message":
            kind, text = "USER_MESSAGE", clean_user_message(str(payload.get("message", "")))
        elif ptype == "agent_message":
            kind, text = "AGENT_MESSAGE", str(payload.get("message", ""))
        elif ptype == "task_complete":
            kind = "TASK_COMPLETE"
            stamp, valid = bounded_task_complete_time(payload, stamp)
            if not valid:
                status = "invalid_task_complete_timestamp"
        elif ptype == "patch_apply_end":
            kind = "PATCH_RESULT"
            status = "success" if payload.get("success") is True else "failed" if payload.get("success") is False else status
        elif ptype == "image_generation_end":
            kind = "IMAGE_RESULT"
        elif ptype == "mcp_tool_call_end":
            kind = "MCP_TOOL_RESULT"
    elif top_type == "response_item":
        ptype = payload.get("type")
        if ptype == "message" and payload.get("role") == "assistant":
            kind, text = "ASSISTANT_MESSAGE_FALLBACK", extract_text(payload.get("content"))
        elif ptype == "function_call_output":
            kind = "FUNCTION_RESULT"
        elif ptype == "custom_tool_call_output":
            kind = "CUSTOM_TOOL_RESULT"
    if not kind:
        return None
    return Event(kind, stamp, session_id, cwd, source, relative, line_number, raw, text, status, tuple(structured_paths(payload)))


def find_jsonl_files(input_root: Path) -> list[Path]:
    roots = [input_root / "sessions", input_root / "archived_sessions"]
    return sorted(path for root in roots if root.is_dir() for path in root.rglob("*.jsonl") if path.is_file())


def known_ignored_record(obj: dict[str, Any]) -> bool:
    top_type = obj.get("type")
    payload = obj.get("payload")
    payload_type = payload.get("type") if isinstance(payload, dict) else None
    if top_type in KNOWN_IGNORED_TOP_TYPES:
        return True
    if top_type == "event_msg" and payload_type in KNOWN_IGNORED_EVENT_TYPES:
        return True
    if top_type == "response_item":
        if payload_type in KNOWN_IGNORED_RESPONSE_TYPES:
            return True
        if payload_type == "message" and isinstance(payload, dict) and payload.get("role") != "assistant":
            return True
    return False


def load_events(input_root: Path, gaps: Counter[str]) -> list[Event]:
    events: list[Event] = []
    for source in find_jsonl_files(input_root):
        session_id, cwd = sha(str(source), 16), ""
        try:
            lines = source.read_bytes().splitlines()
        except OSError:
            gaps["unreadable_jsonl"] += 1
            continue
        relative = str(source.relative_to(input_root))
        for number, raw in enumerate(lines, 1):
            gaps["_records_total"] += 1
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                gaps["invalid_jsonl_line"] += 1
                gaps["_records_unknown"] += 1
                continue
            if obj.get("type") == "session_meta" and isinstance(obj.get("payload"), dict):
                payload = obj["payload"]
                session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                cwd = str(payload.get("cwd") or cwd)
            event = event_from_line(obj, source, relative, number, raw, session_id, cwd)
            if event is None:
                if known_ignored_record(obj):
                    gaps["_records_known_ignored"] += 1
                else:
                    payload = obj.get("payload")
                    payload_type = payload.get("type") if isinstance(payload, dict) else "-"
                    gaps[f"unknown_{obj.get('type', 'missing_type')}.{payload_type}"] += 1
                    gaps["_records_unknown"] += 1
                continue
            gaps["_records_adapted"] += 1
            if event.status == "invalid_task_complete_timestamp":
                gaps[event.status] += 1
            if event.timestamp is None:
                gaps["missing_event_timestamp"] += 1
                continue
            events.append(event)
    return filter_fallbacks(events, gaps)


def filter_fallbacks(events: list[Event], gaps: Counter[str]) -> list[Event]:
    by_session: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_session[event.session_id].append(event)
    output: list[Event] = []
    for session_events in by_session.values():
        agents = [event for event in session_events if event.kind == "AGENT_MESSAGE"]
        fingerprints = {(event.timestamp, sha(normalize(event.text))) for event in agents}
        for event in session_events:
            if event.kind != "ASSISTANT_MESSAGE_FALLBACK":
                output.append(event)
            elif not agents:
                output.append(event)
            elif any(abs((event.timestamp - time).total_seconds()) <= 2 and sha(normalize(event.text)) == digest for time, digest in fingerprints):
                continue
            else:
                gaps["unpaired_assistant_message"] += 1
    return output


def assign_aliases(events: list[Event]) -> dict[str, str]:
    keys = sorted({normalize(event.workspace) or "未声明工作区" for event in events})
    aliases = {key: f"工作区 {alias_suffix(index)}" for index, key in enumerate(keys)}
    for event in events:
        event.workspace = aliases[normalize(event.workspace) or "未声明工作区"]
    return aliases


def alias_suffix(index: int) -> str:
    """Excel-style deterministic labels: A..Z, AA..AZ, BA..."""
    value, chars = index + 1, []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def assign_chains(events: list[Event]) -> None:
    sessions: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        sessions[event.session_id].append(event)
    for session, items in sessions.items():
        items.sort(key=lambda item: item.timestamp or dt.datetime.min.replace(tzinfo=UTC))
        chain = 0
        active_chain = ""
        last_user: Event | None = None
        task_times: list[dt.datetime] = []
        for event in items:
            if event.kind == "TASK_COMPLETE" and event.status != "invalid_task_complete_timestamp" and event.timestamp:
                task_times.append(event.timestamp)
            if event.kind != "USER_MESSAGE" or not event.timestamp:
                event.chain_id = active_chain
                continue
            hard = last_user is None or (event.timestamp - last_user.timestamp).total_seconds() > 1800 or any(marker_at_start(event.text, marker) for marker in STRONG_MARKERS)
            soft = marker_at_start(event.text, WEAK_MARKER) and any(0 <= (event.timestamp - when).total_seconds() <= 300 for when in task_times)
            if hard or soft:
                chain += 1
            active_chain = f"{sha(session, 12)}:{chain}"
            event.chain_id = active_chain
            last_user = event


def selected(events: Iterable[Event], start: dt.datetime, end: dt.datetime) -> list[Event]:
    return [event for event in events if event.timestamp and start <= event.timestamp <= end]


def interval_metrics(events: list[Event]) -> dict[str, int]:
    anchors = [event for event in events if event.kind in {"USER_MESSAGE", "AGENT_MESSAGE", "ASSISTANT_MESSAGE_FALLBACK"}]
    minute_keys = {(event.timestamp.astimezone().year, event.timestamp.astimezone().month, event.timestamp.astimezone().day, event.timestamp.astimezone().hour, event.timestamp.astimezone().minute) for event in anchors}
    sessions: dict[str, list[Event]] = defaultdict(list)
    for event in anchors:
        sessions[event.session_id].append(event)
    intervals: list[tuple[dt.datetime, dt.datetime]] = []
    for session_anchors in sessions.values():
        session_anchors.sort(key=lambda event: event.timestamp)
        for index, event in enumerate(session_anchors[:-1]):
            nxt = session_anchors[index + 1]
            seconds = (nxt.timestamp - event.timestamp).total_seconds()
            if event.kind == "USER_MESSAGE" and nxt.kind != "USER_MESSAGE":
                intervals.append((event.timestamp, event.timestamp + dt.timedelta(seconds=min(max(seconds, 0), 120))))
            elif event.kind != "USER_MESSAGE" and nxt.kind == "USER_MESSAGE":
                intervals.append((event.timestamp, event.timestamp + dt.timedelta(seconds=min(max(seconds, 0), 900))))
    merged: list[list[dt.datetime]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        elif end > merged[-1][1]:
            merged[-1][1] = end
    union_seconds = sum((end - start).total_seconds() for start, end in merged)
    gross_seconds = sum((end - start).total_seconds() for start, end in intervals)
    return {"event_coverage_minutes": len(minute_keys), "interaction_interval_estimate_minutes": round(union_seconds / 60), "overlap_minutes": max(0, round((gross_seconds - union_seconds) / 60))}


def scenario(events: list[Event]) -> tuple[str, str]:
    # User requests are the least contaminated task signal. Agent boilerplate may
    # mention security or configuration even when that is not the user's task.
    source_events = [event for event in events if event.kind == "USER_MESSAGE"] or events
    text = " ".join(normalize(event.text) for event in source_events).lower()
    rules = [("环境配置与排障", ("安装", "认证", "权限", "网络", "配置", "错误")), ("设计与可视化", ("图片", "画布", "图表", "演示")), ("开发与调试", ("patch", "测试", "git", "build", ".py", ".ts")), ("研究与检索", ("搜索", "网页", "资料")), ("写作与内容生产", ("脚本", "改写", "翻译", "文章", "文案")), ("文件与知识管理", ("文档", "表格", "obsidian", "归档")), ("运营协作", ("飞书", "邮件", "日历", "外联"))]
    name, hits = "其他/待确认", 0
    for candidate, tokens in rules:
        score = sum(token in text for token in tokens)
        if score > hits:
            name, hits = candidate, score
    return name, "HIGH" if hits >= 3 else "MEDIUM" if hits >= 1 else "LOW"


def action_findings(events: list[Event]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    chains: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.chain_id:
            chains[event.chain_id].append(event)
    for rule, markers, threshold, action, impact in [("R1_CONSTRAINT_ADDITION", CONSTRAINT_MARKERS, 3, "复杂任务首条消息写清目标、输入、范围、约束、验收。", 2), ("R4_SCOPE_REDIRECTION", REDIRECT_MARKERS, 2, "高成本执行前先确认交付边界。", 2)]:
        matches: list[Event] = []
        for chain in chains.values():
            chain.sort(key=lambda event: event.timestamp)
            baseline_seen, agent_seen, hits = False, False, []
            for event in chain:
                if event.kind == "AGENT_MESSAGE" or event.kind == "ASSISTANT_MESSAGE_FALLBACK":
                    agent_seen = True
                elif event.kind == "USER_MESSAGE":
                    if baseline_seen and agent_seen and any(marker in normalize(event.text) for marker in markers):
                        hits.append(event)
                    baseline_seen = True
            if len(hits) >= threshold:
                matches.extend(hits)
        if matches:
            findings.append(make_finding(rule, matches, impact, 2, action, "复杂任务开始时", "下次同类规则证据应减少。"))
    paths: dict[tuple[str, str], list[Event]] = defaultdict(list)
    for event in events:
        for path in event.paths:
            paths[(event.workspace, normalize(path))].append(event)
    repeated = [event for values in paths.values() for event in values if len(values) >= 3 and (max(item.timestamp for item in values) - min(item.timestamp for item in values)).total_seconds() <= 1800]
    if repeated:
        findings.append(make_finding("R2_REPEAT_TYPED_PATH", repeated, 2, 3, "先确认现状，再集中修改与验证。", "开始修改同一文件前", "短窗重复路径事件应减少。"))
    results = [event for event in events if event.kind in {"PATCH_RESULT", "FUNCTION_RESULT", "CUSTOM_TOOL_RESULT"} and event.status]
    failed = [event for event in results if event.status.lower() in {"failed", "failure", "error"}]
    if len(failed) >= 3 or (len(results) >= 5 and len(failed) / len(results) >= .2):
        findings.append(make_finding("R3_VERIFIED_FAILURE", failed, 3, 3, "执行前定义最小验证命令和失败处理步骤。", "开始有风险的执行前", "可验证失败率应下降。"))
    return sorted(findings, key=lambda item: (-item["score"], -item["count"], item["latest"], item["rule_id"]))[:3]


def make_finding(rule: str, evidence: list[Event], impact: int, level: int, change: str, trigger: str, signal: str) -> dict[str, Any]:
    ids = [evidence_id(event) for event in evidence]
    return {"rule_id": rule, "count": len(evidence), "score": impact * level + min(len(evidence), 3), "latest": max(event.timestamp for event in evidence).isoformat(), "behavior_change": change, "trigger": trigger, "check_signal": signal, "evidence_ids": ids}


def evidence_id(event: Event) -> str:
    summary = redact(normalize(event.text))
    return "ev_" + sha(f"{event.session_id}|{event.timestamp.isoformat()}|{event.kind}|{summary}", 16)


def manifest(events: list[Event]) -> list[dict[str, Any]]:
    file_hashes: dict[Path, str] = {}
    output = []
    for event in events:
        if event.source_path not in file_hashes:
            file_hashes[event.source_path] = "sha256:" + sha(event.source_path.read_bytes())
        output.append({"evidence_id": evidence_id(event), "adapter_version": ADAPTER_VERSION, "source_session_hash": "s_" + sha(event.session_id, 12), "source_jsonl_relative_path": event.relative_source_path, "source_jsonl_sha256": file_hashes[event.source_path], "source_line_number": event.line_number, "source_line_sha256": "sha256:" + sha(event.line_bytes), "event_timestamp": event.timestamp.astimezone().isoformat(), "event_type": event.kind, "summary_hash": "sha256:" + sha(redact(normalize(event.text)))})
    return output


def claim_items_html(claims: list[dict[str, Any]], categories: set[str] | None = None) -> str:
    selected = [claim for claim in claims if categories is None or claim.get("category") in categories]
    if not selected:
        return "<p class='empty'>信息不足，未生成该维度结论。</p>"
    return "<ul class='claim-list'>" + "".join(
        f"<li><p>{html.escape(str(claim.get('text', '')))}</p></li>"
        for claim in selected
    ) + "</ul>"


def deterministic_friction(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "暂未发现达到规则阈值的主要卡点。"
    explanations = {
        "R1_CONSTRAINT_ADDITION": "复杂任务中多次追加约束，容易增加返工。",
        "R2_REPEAT_TYPED_PATH": "同一路径在短时间内被重复处理，操作步骤仍可收敛。",
        "R3_VERIFIED_FAILURE": "可验证失败达到提醒阈值，执行前的验证和兜底需要更明确。",
        "R4_SCOPE_REDIRECTION": "任务执行中出现多次方向调整，交付边界需要更早确认。",
    }
    return explanations.get(findings[0].get("rule_id"), "本周期出现了值得在下次审查中继续观察的协作信号。")


def conclusion_points(report: dict[str, Any]) -> list[dict[str, str]]:
    """Return the three fixed, scan-friendly conclusions for every report surface."""
    semantic = report.get("semantic_analysis") if report.get("semantic_status") == "complete" else None
    leading_scenarios = list(dict.fromkeys(item["scenario"] for item in report["workspaces"][:3]))
    scenario_phrase = "、".join(leading_scenarios) if leading_scenarios else "待确认场景"
    friction = deterministic_friction(report["findings"])
    points = [
        {"number": "01", "question": "你主要在做什么", "answer": f"主要活动集中在{scenario_phrase}。", "support": f"相关信号：{report['workspace_count']} 个本地工作区进入本次统计。"},
        {"number": "02", "question": "你最明显的进展是什么", "answer": "本周期暂未形成足够信号，判断一项稳定进展。", "support": "相关信号：可运行富分析模式，补充跨会话的项目归纳。"},
        {"number": "03", "question": "最需要先解决什么", "answer": friction, "support": "相关信号：来自本地规则对会话内补充和重定向模式的统计。"},
    ]
    if not semantic:
        return points

    top_projects = report.get("semantic_projects", [])[:3]
    if top_projects:
        names = "、".join(str(item.get("name", "未命名项目")) for item in top_projects)
        share = sum(float(item.get("interaction_share", 0)) for item in top_projects) * 100
        points[0] = {"number": "01", "question": "你主要在做什么", "answer": f"{names}，构成了这段时间最主要的投入。", "support": f"相关信号：前三个项目合计占 {share:.1f}% 的交互活动估算。"}

    patterns = semantic.get("work_patterns", {})
    progress_claims = patterns.get("progress", []) or patterns.get("reusable_workflows", []) or patterns.get("stable_strengths", [])
    if progress_claims:
        progress = progress_claims[0]
        points[1] = {"number": "02", "question": "你最明显的进展是什么", "answer": str(progress.get("text", points[1]["answer"])), "support": f"相关信号：模型归纳 · {str(progress.get('confidence', 'MEDIUM'))} 置信度。"}

    problems = patterns.get("problems", [])
    if problems:
        problem = problems[0]
        points[2] = {"number": "03", "question": "最需要先解决什么", "answer": str(problem.get("text", friction)), "support": f"相关信号：模型归纳 · {str(problem.get('confidence', 'MEDIUM'))} 置信度。"}
    return points


def report_html(report: dict[str, Any]) -> str:
    metrics, findings = report["metrics"], report["findings"]
    maximum_hour = max(report["hourly_activity"].values(), default=1) or 1
    maximum_day = max(report["daily_activity"].values(), default=1) or 1
    maximum_scenario = max(report["scenario_distribution"].values(), default=1) or 1
    heatmap = "".join(
        f"<div class='heat-cell' title='{int(hour):02d}:00 · {count} 个活跃分钟'>"
        f"<span class='heat-hour'>{int(hour):02d}</span><span class='heat-track'><i style='height:{count / maximum_hour * 100:.1f}%'></i></span>"
        f"<strong>{count}</strong></div>"
        for hour, count in report["hourly_activity"].items()
    )
    trend = "".join(
        f"<div class='bar-row'><span>{html.escape(day[5:])}</span><div class='bar-track'><i style='width:{count / maximum_day * 100:.1f}%'></i></div><strong>{count} mins</strong></div>"
        for day, count in report["daily_activity"].items()
    ) or "<p class='empty'>无时间锚点。</p>"
    scenarios = "".join(
        f"<div class='scenario-row'><div><strong>{html.escape(name)}</strong><span>{count} 个工作区</span></div>"
        f"<div class='scenario-track'><i style='width:{count / maximum_scenario * 100:.1f}%'></i></div></div>"
        for name, count in sorted(report["scenario_distribution"].items(), key=lambda item: (-item[1], item[0]))
    ) or "<p class='empty'>其他/待确认：0</p>"
    workspaces = "".join(
        f"<article class='project-card'><div class='project-index'>{index:02d}</div><div class='project-copy'>"
        f"<div class='eyebrow'>{html.escape(item['scenario'])} · {html.escape(item['confidence'])}</div>"
        f"<h3>{html.escape(item['alias'])}</h3><p>{html.escape(item['time_range'])}</p>"
        f"<div class='project-stats'><span><b>{item['event_count']}</b> 可定位事件</span>"
        f"<span><b>{item['confirmed_artifact_events']}</b> 已确认产物事件</span></div></div></article>"
        for index, item in enumerate(report["workspaces"][:3], 1)
    ) or "<p class='empty'>无可定位工作区。</p>"
    actions = "".join(
        f"<article class='action-card'><div class='action-number'>{index}</div><div><div class='action-rule'>{html.escape(item['rule_id'])}</div>"
        f"<h3>{html.escape(item['behavior_change'])}</h3><p><b>触发条件</b> {html.escape(item['trigger'])}</p>"
        f"<p><b>下次检查</b> {html.escape(item['check_signal'])}</p></div></article>"
        for index, item in enumerate(findings, 1)
    ) or "<div class='empty-dark'>本周期没有足够高置信度的信息，未生成行动建议。</div>"
    semantic = report.get("semantic_analysis") if report.get("semantic_status") == "complete" else None
    friction = deterministic_friction(findings)
    verdict_mode = "本地统计完成"
    verdict_confidence = "规则判断"
    project_heading = "主要工作区"
    project_note = "工作区仅作为本地分析容器；富分析模式可在此上方归纳跨会话项目主题。"
    project_metric = report["workspace_count"]
    rich_review_section = ""
    patterns_section = ""
    if semantic:
        core = semantic.get("core_judgment", {})
        verdict_mode = "分析完成"
        verdict_confidence = f"{core.get('confidence', 'LOW')} 置信度"
        project_heading = "项目版图"
        project_note = "项目主题由当前 Codex 模型归纳；每条会话只分配给一个主项目，投入占比不重复计算。"
        assignments = report.get("semantic_projects", [])
        project_metric = len(assignments)
        workspaces = "".join(
            f"<article class='project-card'><div class='project-index'>{index:02d}</div><div class='project-copy'>"
            f"<div class='eyebrow'>模型归纳 · {html.escape(str(item.get('confidence', 'LOW')))}</div>"
            f"<h3>{html.escape(str(item.get('name', '未命名项目')))}</h3><p>{html.escape(str(item.get('summary', '')))}</p>"
            f"<div class='project-stats'><span><b>{item.get('interaction_activity_estimate', 0)}</b> 估算分钟</span>"
            f"<span><b>{item.get('interaction_share', 0) * 100:.1f}%</b> 交互占比</span><span><b>{len(item.get('member_thread_refs', []))}</b> 会话</span></div></div></article>"
            for index, item in enumerate(assignments[:6], 1)
        ) or "<p class='empty'>富分析未形成可验证项目。</p>"
        rich_cards = []
        for project in semantic.get("projects", []):
            claims = project.get("claims", [])
            rich_cards.append(
                f"<article class='review-card'><div class='eyebrow'>深度项目复盘 · {html.escape(str(project.get('confidence', 'LOW')))}</div>"
                f"<h3>{html.escape(str(project.get('name', '未命名项目')))}</h3>"
                f"<div class='review-grid'><section><h4>做了什么</h4>{claim_items_html(claims, {'goal', 'work', 'artifact', 'turning_point'})}</section>"
                f"<section><h4>卡点是什么</h4>{claim_items_html(claims, {'problem', 'prompt_review'})}</section>"
                f"<section><h4>优化方案</h4>{claim_items_html(claims, {'next_step'})}</section></div></article>"
            )
        rich_review_section = "<section class='shell section' id='reviews'><div class='section-head'><div><div class='eyebrow'>Project review</div><h2>主要项目复盘</h2></div><p class='section-note'>当前相关文件用于辅助理解项目背景，不等同本周期确认产物。</p></div><div class='review-list'>" + "".join(rich_cards) + "</div></section>"
        patterns = semantic.get("work_patterns", {})
        if patterns.get("problems"):
            friction = str(patterns["problems"][0].get("text", friction))
        patterns_section = (
            "<section class='shell section' id='patterns'><div class='section-head'><div><div class='eyebrow'>Work patterns</div><h2>工作模式</h2></div></div>"
            "<div class='analytics-grid'><article class='panel'><h3>主要问题</h3>" + claim_items_html(patterns.get("problems", [])) + "</article>"
            "<article class='panel'><h3>本周期进步</h3>" + claim_items_html(patterns.get("progress", [])) + "</article>"
            "<article class='panel'><h3>稳定优势</h3>" + claim_items_html(patterns.get("stable_strengths", [])) + "</article>"
            "<article class='panel'><h3>可复用工作流</h3>" + claim_items_html(patterns.get("reusable_workflows", [])) + "</article></div></section>"
        )
        semantic_actions = semantic.get("actions", [])
        actions = "".join(
            f"<article class='action-card'><div class='action-number'>{index}</div><div><div class='action-rule'>建议 · 模型归纳</div>"
            f"<h3>{html.escape(str(item.get('behavior_change', '')))}</h3><p><b>触发条件</b> {html.escape(str(item.get('trigger_condition', '')))}</p>"
            f"<p><b>下次检查</b> {html.escape(str(item.get('next_review_signal', '')))}</p></div></article>"
            for index, item in enumerate(semantic_actions, 1)
        ) or actions
    conclusion_html = "".join(
        f"<li class='conclusion-item'><span class='conclusion-number'>{item['number']}</span><div class='conclusion-copy'><p class='conclusion-question'>{html.escape(item['question'])}</p><h2>{html.escape(item['answer'])}</h2><p class='conclusion-support'>{html.escape(item['support'])}</p></div></li>"
        for item in conclusion_points(report)
    )
    gaps = html.escape(json.dumps(report["data_gaps"], ensure_ascii=False, indent=2))
    quality = report.get("data_quality", {})
    analysis_versions = html.escape(json.dumps(report.get("analysis_metadata", {}), ensure_ascii=False, indent=2))
    report_id = html.escape(report["report_id"])
    margin_ornaments = """
    <aside class='margin-ornaments' aria-hidden='true'>
      <svg class='margin-symbols' aria-hidden='true'><defs><path id='openai-blossom' d='M249.176 323.434V298.276C249.176 296.158 249.971 294.569 251.825 293.509L302.406 264.381C309.29 260.409 317.5 258.555 325.973 258.555C357.75 258.555 377.877 283.185 377.877 309.399C377.877 311.253 377.877 313.371 377.611 315.49L325.178 284.771C322.001 282.919 318.822 282.919 315.645 284.771L249.176 323.434ZM367.283 421.415V361.301C367.283 357.592 365.694 354.945 362.516 353.092L296.048 314.43L317.763 301.982C319.617 300.925 321.206 300.925 323.058 301.982L373.639 331.112C388.205 339.586 398.003 357.592 398.003 375.069C398.003 395.195 386.087 413.733 367.283 421.412V421.415ZM233.553 368.452L211.838 355.742C209.986 354.684 209.19 353.095 209.19 350.975V292.718C209.19 264.383 230.905 242.932 260.301 242.932C271.423 242.932 281.748 246.641 290.49 253.26L238.321 283.449C235.146 285.303 233.555 287.951 233.555 291.659V368.455L233.553 368.452ZM280.292 395.462L249.176 377.985V340.913L280.292 323.436L311.407 340.913V377.985L280.292 395.462ZM300.286 475.968C289.163 475.968 278.837 472.259 270.097 465.64L322.264 435.449C325.441 433.597 327.03 430.949 327.03 427.239V350.445L349.011 363.155C350.865 364.213 351.66 365.802 351.66 367.922V426.179C351.66 454.514 329.679 475.965 300.286 475.965V475.968ZM237.525 416.915L186.944 387.785C172.378 379.31 162.582 361.305 162.582 343.827C162.582 323.436 174.763 305.164 193.563 297.485V357.861C193.563 361.571 195.154 364.217 198.33 366.071L264.535 404.467L242.82 416.915C240.967 417.972 239.377 417.972 237.525 416.915ZM234.614 460.343C204.689 460.343 182.71 437.833 182.71 410.028C182.71 407.91 182.976 405.792 183.238 403.672L235.405 433.863C238.582 435.715 241.763 435.715 244.938 433.863L311.407 395.466V420.622C311.407 422.742 310.612 424.331 308.758 425.389L258.179 454.519C251.293 458.491 243.083 460.343 234.611 460.343H234.614ZM300.286 491.854C332.329 491.854 359.073 469.082 365.167 438.892C394.825 431.211 413.892 403.406 413.892 375.073C413.892 356.535 405.948 338.529 391.648 325.552C392.972 319.991 393.766 314.43 393.766 308.87C393.766 271.003 363.048 242.666 327.562 242.666C320.413 242.666 313.528 243.723 306.644 246.109C294.725 234.457 278.307 227.042 260.301 227.042C228.258 227.042 201.513 249.815 195.42 280.004C165.761 287.685 146.694 315.49 146.694 343.824C146.694 362.362 154.638 380.368 168.938 393.344C167.613 398.906 166.819 404.467 166.819 410.027C166.819 447.894 197.538 476.231 233.024 476.231C240.172 476.231 247.058 475.173 253.943 472.788C265.859 484.441 282.278 491.854 300.286 491.854Z' fill='black'/></defs></svg>
      <svg class='margin-network network-left' viewBox='0 0 160 900' fill='none'><g class='network-line'><path d='M26 52H116Q124 52 124 60V248Q124 256 132 256H146'/><path d='M112 256V350Q112 358 104 358H82Q74 358 74 366V520'/><path d='M18 530H78Q86 530 86 538V694Q86 702 94 702H136'/><path d='M112 702V818Q112 826 104 826H52'/></g><g class='network-node'><circle cx='26' cy='52' r='3'/><circle cx='124' cy='122' r='2.5'/><circle cx='74' cy='366' r='3'/><circle cx='136' cy='702' r='3'/><circle cx='112' cy='818' r='3'/></g><text x='0' y='48'>&gt;_</text><text x='0' y='526'>&gt;_</text><text x='0' y='814'>&gt;_</text><rect class='network-cursor' x='132' y='248' width='7' height='7'/><rect class='network-cursor' x='94' y='694' width='7' height='7'/></svg>
      <svg class='margin-network network-right' viewBox='0 0 160 900' fill='none'><g class='network-line'><path d='M138 72H84Q76 72 76 80V150Q76 158 68 158H28'/><path d='M14 178H82Q90 178 90 186V334Q90 342 98 342H132'/><path d='M38 486H108Q116 486 116 494V604Q116 612 124 612H148'/><path d='M64 720H126Q134 720 134 728V842H88'/></g><g class='network-node'><circle cx='68' cy='158' r='3'/><circle cx='28' cy='178' r='3'/><circle cx='132' cy='342' r='3'/><circle cx='38' cy='486' r='3'/><circle cx='148' cy='612' r='3'/><circle cx='126' cy='720' r='3'/></g><text x='110' y='68'>&gt;_</text><text x='0' y='482'>&gt;_</text><text x='48' y='716'>&gt;_</text><rect class='network-cursor' x='132' y='68' width='7' height='7'/><rect class='network-cursor' x='24' y='482' width='7' height='7'/></svg>
      <svg class='margin-mark mark-top-left' viewBox='146 227 268 265'><use href='#openai-blossom'/></svg>
      <svg class='margin-mark mark-top-right' viewBox='146 227 268 265'><use href='#openai-blossom'/></svg>
      <svg class='margin-mark mark-bottom-left' viewBox='146 227 268 265'><use href='#openai-blossom'/></svg>
      <svg class='margin-mark mark-bottom-right' viewBox='146 227 268 265'><use href='#openai-blossom'/></svg>
    </aside>
    """
    theme_css = """
    @theme {
      --color-ink: #0a1217; --color-paper: #ffffff; --color-frost: #e4eff7;
      --color-stone: #85898b; --color-obsidian: #000000; --color-lime: #cdfe00;
      --radius-card: 24px; --radius-pill: 9999px; --spacing-section: 64px;
    }
    :root {
      --color-ink:#0a1217; --color-paper:#ffffff; --color-frost:#e4eff7;
      --color-stone:#85898b; --color-obsidian:#000000; --color-lime:#cdfe00;
      --font-display:"Iowan Old Style","Baskerville","Times New Roman",serif;
      --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
      --text-caption:13px; --text-body:16px; --text-heading:20px; --text-display:72px;
      --radius-card:24px; --radius-pill:9999px; --space-1:8px; --space-2:16px;
      --space-3:24px; --space-4:32px; --space-section:64px; --page-max:1200px; --control-height:44px;
    }
    *{box-sizing:border-box} html{scroll-behavior:smooth} body{margin:0;background:var(--color-paper);color:var(--color-ink);font-family:var(--font-sans);font-size:var(--text-body);line-height:1.5;font-feature-settings:"lnum" 1,"tnum" 1} a{color:inherit;text-decoration:none} button{font:inherit}
    .shell{width:min(calc(100% - 40px),var(--page-max));margin:0 auto}.topbar{min-height:76px;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:24px}.brand{font-weight:600;letter-spacing:-.02em}.nav{display:flex;gap:24px;font-size:12px;font-weight:500;color:var(--color-stone)}.nav a:hover{color:var(--color-ink)}.top-actions{display:flex;justify-content:flex-end;gap:8px}.pill{height:var(--control-height);display:inline-flex;align-items:center;justify-content:center;padding:0 16px;border:1px solid var(--color-line);border-radius:var(--radius-pill);background:transparent;font-size:12px;font-weight:500;cursor:pointer}.pill-primary{border-color:var(--color-blue);background:var(--color-blue);color:var(--color-paper)}
    .conclusion-wrap{padding:52px 0 0}.conclusion-intro{max-width:760px;margin:0 auto 38px;text-align:center}.report-meta{margin:0 0 14px;color:var(--color-stone);font-size:13px}.conclusion-intro h1{margin:0;font-size:clamp(40px,5.6vw,64px);letter-spacing:-.045em;line-height:1.05;font-weight:600}.conclusion-intro>p:last-child{max-width:430px;margin:16px auto 0;color:var(--color-stone);font-size:17px}.conclusion-status{display:inline-flex;gap:8px;margin-top:18px;color:var(--color-stone);font-size:12px}.conclusion-status b{color:var(--color-ink);font-weight:600}.conclusion-list{list-style:none;max-width:900px;margin:0 auto;padding:0;border-top:1px solid var(--color-line)}.conclusion-item{display:grid;grid-template-columns:112px 1fr;gap:28px;padding:30px 0;border-bottom:1px solid var(--color-line)}.conclusion-number{color:var(--color-blue);font-size:28px;font-weight:600;letter-spacing:-.04em}.conclusion-question{margin:0 0 8px;color:var(--color-stone);font-size:14px;font-weight:500}.conclusion-copy h2{max-width:680px;margin:0;font-size:clamp(22px,2.8vw,32px);line-height:1.22;letter-spacing:-.03em;font-weight:600}.conclusion-support{margin:10px 0 0;color:var(--color-stone);font-size:13px}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{min-height:142px;padding:24px;border:1px solid var(--color-line);border-radius:var(--radius-card);background:var(--color-frost);display:flex;flex-direction:column;justify-content:space-between}.metric-label{font-size:13px;color:var(--color-stone)}.metric strong{font-family:var(--font-display);font-size:36px;font-weight:600;line-height:1;letter-spacing:-.04em}.metric small{font-size:13px;color:var(--color-stone)}
    .section{padding-top:var(--space-section)}.section-head{display:flex;align-items:end;justify-content:space-between;gap:24px;margin-bottom:24px}.eyebrow{font-size:13px;color:var(--color-stone)}h2{margin:5px 0 0;font-size:32px;line-height:1.15;font-weight:600;letter-spacing:-.03em}.section-note{max-width:470px;margin:0;color:var(--color-stone);font-size:14px}.analytics-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:12px}.panel{border:1px solid var(--color-line);border-radius:var(--radius-card);background:var(--color-frost);padding:28px;min-width:0}.panel-dark{background:var(--color-frost);color:var(--color-ink)}.panel h3{margin:0 0 24px;font-size:20px;font-weight:600}.bar-row{display:grid;grid-template-columns:48px 1fr max-content;align-items:center;gap:12px;margin:10px 0;font-size:13px}.bar-track,.scenario-track{height:7px;border-radius:var(--radius-pill);background:var(--color-paper);overflow:hidden}.bar-track i,.scenario-track i{display:block;height:100%;border-radius:inherit;background:var(--color-blue)}.bar-row strong{text-align:right;white-space:nowrap}.scenario-row{margin:18px 0}.scenario-row>div:first-child{display:flex;justify-content:space-between;gap:16px;margin-bottom:8px}.scenario-row span{color:var(--color-stone);font-size:13px}.scenario-method{margin-top:28px;padding-top:24px;border-top:1px solid var(--color-line)}.scenario-panel{grid-column:1 / -1}.hours{display:grid;grid-template-columns:repeat(12,1fr);gap:8px}.heat-cell{min-width:0;text-align:center;font-size:12px}.heat-hour{display:block;color:var(--color-stone)}.heat-track{display:block;position:relative;height:76px;margin:8px 0;border-radius:var(--radius-pill);overflow:hidden;background:var(--color-paper)}.heat-track i{position:absolute;left:0;right:0;bottom:0;background:var(--color-blue);min-height:2px}.heat-cell strong{font-size:11px;font-weight:500}
    .project-list,.review-list{display:grid;gap:12px}.project-card{display:grid;grid-template-columns:88px 1fr;min-height:190px;border:1px solid var(--color-line);border-radius:var(--radius-card);overflow:hidden;background:var(--color-frost)}.project-index{display:flex;align-items:flex-end;padding:24px;color:var(--color-blue);font-family:var(--font-display);font-size:30px;font-weight:600}.project-copy{padding:30px}.project-copy h3{margin:6px 0 10px;font-size:24px;letter-spacing:-.025em}.project-copy p{margin:0;color:var(--color-stone)}.project-stats{display:flex;flex-wrap:wrap;gap:24px;margin-top:28px;font-size:14px}.project-stats b{font-size:20px}.review-card{padding:30px;border:1px solid var(--color-line);border-radius:var(--radius-card);background:var(--color-frost)}.review-card h3{font-size:28px;margin:8px 0 24px}.review-grid{display:grid;grid-template-columns:1fr;gap:12px}.review-grid section{padding:20px;border:1px solid var(--color-line);border-radius:20px;background:var(--color-paper)}.review-grid h4{margin:0 0 16px}.claim-list{list-style:none;padding:0;margin:0}.claim-list li{padding:12px 0;border-top:1px solid var(--color-line)}.claim-list li:first-child{border-top:0}.claim-list p{margin:5px 0}
    .actions{border:1px solid var(--color-line);border-radius:var(--radius-card);background:var(--color-frost);color:var(--color-ink);padding:36px}.actions-head{display:flex;justify-content:space-between;align-items:end;gap:24px;margin-bottom:28px}.lime-pill{height:var(--control-height);display:inline-flex;align-items:center;padding:0 16px;border-radius:var(--radius-pill);background:var(--color-paper);border:1px solid var(--color-line);color:var(--color-stone);font-size:12px;font-weight:500}.action-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}.action-card{padding:24px;border:1px solid var(--color-line);border-radius:22px;background:var(--color-paper)}.action-number{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;background:var(--color-ink);color:var(--color-paper);font-size:13px;font-weight:600;margin-bottom:28px}.action-rule{font-size:12px;color:var(--color-stone)}.action-card h3{font-size:20px;line-height:1.25;margin:8px 0 22px;letter-spacing:-.02em}.action-card p{font-size:14px;color:var(--color-stone)}.action-card p b{display:block;color:var(--color-ink)}.empty,.empty-dark{padding:24px;border-radius:var(--radius-card);color:var(--color-stone)}.empty-dark{border:1px solid var(--color-line);background:var(--color-paper)}
    details{border:1px solid var(--color-line);border-radius:var(--radius-card);background:var(--color-frost);padding:24px}summary{cursor:pointer;font-weight:600}.activity-details{padding:0;overflow:hidden}.activity-details>summary{display:flex;align-items:center;justify-content:space-between;gap:24px;padding:28px 32px;list-style:none}.activity-details>summary::-webkit-details-marker{display:none}.activity-details>summary span{font-size:22px;letter-spacing:-.02em}.activity-details>summary small{color:var(--color-stone);font-weight:400}.activity-content{padding:0 12px 12px}.activity-details[open]>summary{border-bottom:1px solid var(--color-line);margin-bottom:12px}pre{white-space:pre-wrap;overflow:auto;margin:20px 0 0;padding:20px;border-radius:16px;background:var(--color-paper);font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace}.method-copy{color:var(--color-stone);font-size:14px}.footer{display:flex;justify-content:space-between;gap:24px;padding:64px 0 32px;font-size:13px;color:var(--color-stone)}
    @media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
    @media(max-width:900px){.topbar{grid-template-columns:1fr auto}.nav{display:none}.metrics{grid-template-columns:repeat(2,1fr)}.analytics-grid,.review-grid{grid-template-columns:1fr}.action-list{grid-template-columns:1fr}.section-head,.actions-head{align-items:flex-start;flex-direction:column}.hours{grid-template-columns:repeat(8,1fr)}}
    @media(max-width:560px){.shell{width:min(calc(100% - 24px),var(--page-max))}.top-actions .pill:first-child{display:none}.conclusion-wrap{padding-top:32px}.conclusion-intro{margin-bottom:28px;text-align:left}.conclusion-intro>p:last-child{margin-left:0}.conclusion-item{grid-template-columns:52px 1fr;gap:14px;padding:24px 0}.conclusion-number{font-size:22px}.conclusion-copy h2{font-size:23px}.metrics{grid-template-columns:1fr}.panel,.actions{padding:24px}.hours{grid-template-columns:repeat(6,1fr)}.project-card{grid-template-columns:1fr}.project-index{min-height:64px;align-items:center;padding:20px}.project-stats{flex-direction:column;gap:8px}.activity-details>summary{align-items:flex-start;flex-direction:column;padding:24px}.footer{flex-direction:column}}
    @media print{.top-actions,.nav{display:none}.panel,.project-card,.review-card,.actions,.conclusion-item{break-inside:avoid}.review-card{padding:24px}.review-card h3{font-size:24px;margin:6px 0 16px}.review-grid{gap:12px}.review-grid section{padding:16px}.claim-list li{padding:8px 0}.section-head{break-after:avoid}#projects,#reviews,#patterns,#actions{break-before:page}body{print-color-adjust:exact;-webkit-print-color-adjust:exact}}

    /* Sana visual system: restore its token, typography, and contrast language.
       Apple-inspired restraint applies only to the reading sequence of the conclusions. */
    @theme{--color-ink:#0a1217;--color-paper:#ffffff;--color-frost:#e4eff7;--color-stone:#85898b;--color-obsidian:#000000;--color-lime:#cdfe00;--radius-card:24px;--radius-pill:9999px;--spacing-section:64px}
    :root{--color-ink:#0a1217;--color-paper:#ffffff;--color-frost:#e4eff7;--color-stone:#85898b;--color-obsidian:#000000;--color-lime:#cdfe00;--font-display:"Iowan Old Style","Baskerville","Times New Roman",serif;--text-display:72px;--radius-card:24px;--space-section:64px;--page-max:1200px;--control-height:44px}
    .shell{width:min(calc(100% - 40px),var(--page-max))}.topbar{min-height:80px}.brand{font-weight:500;letter-spacing:0}.nav{font-size:14px;font-weight:450;color:var(--color-ink)}.pill{height:var(--control-height);padding:0 18px;border:1px solid var(--color-ink);font-size:14px;font-weight:450}.pill-primary{border-color:var(--color-ink);background:var(--color-ink);color:var(--color-paper)}
    .conclusion-wrap{padding:40px 0 0}.conclusion-panel{position:relative;max-width:1040px;margin:0 auto;overflow:hidden;border-radius:var(--radius-card);background:var(--color-ink);color:var(--color-paper)}.conclusion-panel::before{content:"";position:absolute;top:0;left:44px;width:118px;height:2px;background:var(--color-lime)}.conclusion-intro{max-width:none;margin:0;padding:42px 44px 24px;text-align:left}.report-meta{margin:0 0 18px;color:#b7bdc1;font-size:13px}.conclusion-intro h1{font-family:var(--font-display);font-size:clamp(40px,4.5vw,56px);font-weight:400;letter-spacing:-.035em;line-height:1.06}.conclusion-intro>p:last-child{max-width:440px;margin:14px 0 0;color:#d6dcdf;font-size:16px}.conclusion-status{margin-top:22px;color:#b7bdc1}.conclusion-status b{color:var(--color-lime);font-weight:500}.conclusion-list{max-width:none;margin:0;padding:0 44px;border-top:1px solid #344047}.conclusion-item{grid-template-columns:64px 1fr;gap:20px;padding:24px 0;border-bottom:1px solid #344047}.conclusion-number{color:var(--color-lime);font-family:var(--font-display);font-size:27px;font-weight:400;letter-spacing:0}.conclusion-question{margin:0 0 6px;color:#b7bdc1;font-size:13px;font-weight:400}.conclusion-copy h2{max-width:730px;color:var(--color-paper);font-family:var(--font-display);font-size:clamp(24px,2.7vw,32px);font-weight:400;line-height:1.16;letter-spacing:-.025em}.conclusion-support{margin:9px 0 0;color:#b7bdc1;font-size:13px}
    .metrics{gap:16px}.metric{min-height:150px;padding:24px;border:0;border-radius:var(--radius-card);background:var(--color-frost)}.metric:nth-child(2){background:var(--color-ink);color:var(--color-paper)}.metric:nth-child(2) .metric-label{color:#b7bdc1}.metric strong{font-family:var(--font-display);font-size:42px;font-weight:400;letter-spacing:0}.section{padding-top:var(--space-section)}h2{font-weight:500;letter-spacing:-.02em}.analytics-grid{gap:16px}.panel{border:0;border-radius:var(--radius-card);background:var(--color-frost);padding:32px}.panel-dark{background:var(--color-ink);color:var(--color-paper)}.bar-track,.scenario-track{height:8px}.bar-track i,.scenario-track i{background:var(--color-ink)}.heat-hour{color:#b7bdc1}.heat-track{background:#253038}.heat-track i{background:var(--color-lime)}
    .project-list,.review-list{gap:16px}.project-card{min-height:210px;border:0;border-radius:var(--radius-card);background:var(--color-frost)}.project-index{padding:24px;background:var(--color-ink);color:var(--color-lime);font-family:var(--font-display);font-size:34px;font-weight:400}.project-copy{padding:32px}.project-copy h3{letter-spacing:0}.project-stats{margin-top:32px}.review-card{padding:32px;border:0;border-radius:var(--radius-card);background:var(--color-frost)}.review-grid{gap:16px}.review-grid section{border:0;border-radius:20px;background:var(--color-paper)}.claim-list li{border-color:#d3dfe7}
    .actions{padding:40px;border:0;border-radius:var(--radius-card);background:var(--color-ink);color:var(--color-paper)}.actions-head{margin-bottom:32px}.lime-pill{height:var(--control-height);padding:0 18px;border:0;background:var(--color-lime);color:var(--color-ink);font-size:14px;font-weight:450}.action-list{grid-template-columns:repeat(3,1fr);gap:12px}.action-card{padding:24px;border:0;border-radius:var(--radius-card);background:rgba(255,255,255,.08)}.action-card:first-child{background:var(--color-lime);color:var(--color-ink)}.action-number{width:36px;height:36px;background:var(--color-lime);color:var(--color-ink);font-size:16px;font-weight:500;margin-bottom:32px}.action-card:first-child .action-number{background:var(--color-ink);color:var(--color-lime)}.action-rule{color:#b7bdc1}.action-card:first-child .action-rule,.action-card:first-child p{color:#334047}.action-card p{color:#d6dcdf}.action-card p b{color:var(--color-paper)}.action-card:first-child p b{color:var(--color-ink)}.empty-dark{border:0;background:rgba(255,255,255,.08);color:#d6dcdf}
    details{border:0;border-radius:var(--radius-card);background:var(--color-frost)}.activity-content{padding:0 16px 16px}.activity-details[open]>summary{border-color:#cbd8e1}.activity-details>summary span{font-size:24px;letter-spacing:0}.footer{padding:64px 0 32px}
    @media(max-width:900px){.action-list{grid-template-columns:1fr}}
    @media(max-width:560px){.conclusion-wrap{padding-top:20px}.conclusion-panel::before{left:20px}.conclusion-intro{padding:32px 20px 22px}.conclusion-list{padding:0 20px}.conclusion-item{grid-template-columns:46px 1fr;gap:12px;padding:20px 0}.conclusion-number{font-size:22px}.conclusion-copy h2{font-size:25px}.project-index{min-height:72px;padding:20px}.panel,.actions{padding:24px}}
    .margin-ornaments{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none}.margin-symbols{position:absolute;width:0;height:0;overflow:hidden}.topbar,main,.footer{position:relative;z-index:1}.margin-network{position:fixed;top:32px;width:min(140px,calc((100vw - var(--page-max))/2 - 28px));height:calc(100vh - 64px);color:var(--color-ink)}.network-left{left:20px}.network-right{right:20px}.network-line{stroke:currentColor;stroke-width:1;stroke-dasharray:2 3;stroke-linecap:round;stroke-linejoin:round;opacity:.18}.network-node{stroke:currentColor;stroke-width:1;fill:var(--color-paper);opacity:.24}.margin-network text{fill:currentColor;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;letter-spacing:-1px;opacity:.34}.network-cursor{fill:var(--color-lime)}.margin-mark{position:fixed;width:34px;height:34px;opacity:.18}.mark-top-left{top:24px;left:23px}.mark-top-right{top:24px;right:23px}.mark-bottom-left{bottom:24px;left:23px}.mark-bottom-right{right:23px;bottom:24px}@media(max-width:1360px){.margin-ornaments{display:none}}@media print{.margin-ornaments{display:none}}
    """
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='light'><title>Codex 周期复盘 · {html.escape(report['range'])}</title><style>{theme_css}</style></head><body>{margin_ornaments}
    <header class='shell topbar'><a class='brand' href='#top'>Codex Review</a><nav class='nav' aria-label='报告导航'><a href='#conclusion'>结论</a><a href='#actions'>行动</a><a href='#projects'>项目</a><a href='#details'>详情</a></nav><div class='top-actions'><a class='pill' href='./{report_id}.md'>Markdown</a><a class='pill pill-primary' href='./{report_id}.pdf' download='Codex审查报告-{html.escape(report["range"].replace(" ", "")).replace("至", "-")}.pdf'>下载 PDF</a></div></header>
    <main id='top'><section class='shell conclusion-wrap' id='conclusion'><div class='conclusion-panel'><div class='conclusion-intro'><p class='report-meta'>{html.escape(report['range'])} · 本地私密报告</p><h1>这段时间的复盘</h1><p>先看三件最值得关注的事。</p><p class='conclusion-status'><b>{verdict_mode}</b><span>·</span><span>{html.escape(verdict_confidence)}</span></p></div><ol class='conclusion-list' aria-label='本周期三条结论'>{conclusion_html}</ol></div></section>
    <section class='shell section' id='actions'><div class='actions'><div class='actions-head'><div><div class='eyebrow'>Next cycle</div><h2>接下来只做这三件事</h2></div><span class='lime-pill'>按优先级排序 · 可验证</span></div><div class='action-list'>{actions}</div></div></section>
    <section class='shell section' id='overview'><div class='section-head'><div><div class='eyebrow'>Data overview</div><h2>数据概览</h2></div><p class='section-note'>数据用于解释结论，活动估算不代表真实工时。</p></div><div class='metrics'><article class='metric'><span class='metric-label'>事件覆盖</span><strong>{metrics['event_coverage_minutes']}</strong><small>去重自然分钟</small></article><article class='metric'><span class='metric-label'>交互活动估算</span><strong>{metrics['interaction_interval_estimate_minutes']}</strong><small>分钟 · 非真实工时</small></article><article class='metric'><span class='metric-label'>{'项目' if semantic else '工作区'}</span><strong>{project_metric}</strong><small>{'模型归纳，唯一归属' if semantic else '本地分析容器'}</small></article><article class='metric'><span class='metric-label'>已确认产物</span><strong>{report['confirmed_artifact_events']}</strong><small>补丁与明确写入事件</small></article></div></section>
    <section class='shell section' id='projects'><div class='section-head'><div><div class='eyebrow'>Project map</div><h2>{project_heading}</h2></div><p class='section-note'>{project_note}</p></div><div class='project-list'>{workspaces}</div></section>{rich_review_section}{patterns_section}
    <section class='shell section' id='details'><details class='activity-details'><summary><span>展开查看活动数据</span><small>趋势、24 小时活跃图与场景分布</small></summary><div class='activity-content'><div class='analytics-grid'><article class='panel'><h3>活跃趋势</h3>{trend}</article><article class='panel panel-dark'><h3>24 小时活跃图</h3><div class='hours'>{heatmap}</div></article><article class='panel scenario-panel'><h3>场景分布</h3>{scenarios}<div class='scenario-method'><p class='method-copy'>事件覆盖分钟数和交互间隔估算分钟数均来自本地 JSONL 时间锚点，用于观察 Codex 协作活动，不等同真实工作时长。</p><div class='project-stats'><span><b>{metrics['overlap_minutes']}</b> 并行重叠分钟</span><span><b>{len(report['daily_activity'])}</b> 活跃日</span><span><b>{report['event_count']}</b> 可定位事件</span></div></div></article></div></div></details></section>
    <section class='shell section'><details><summary>查看数据缺口与隐私说明</summary><p class='method-copy'>未显式标记的新任务可能保留在同一消息链中；相关诊断只表示会话内后续补充或重定向模式。审计详情保存在同目录 manifest 文件中。</p><h3>解析覆盖</h3><p class='method-copy'>已适配或按版本规则明确忽略 {quality.get('recognized_records', 0)} / {quality.get('total_records', 0)} 条记录，结构识别率 {quality.get('structural_parse_rate', 0) * 100:.1f}%。真正未知事件 {quality.get('unknown_records', 0)} 条。</p><h3>数据缺口</h3><pre>{gaps}</pre><h3>分析版本</h3><pre>{analysis_versions}</pre></details></section></main>
    <footer class='shell footer'><span>Codex Review · 本地私密报告</span><span>{html.escape(report['adapter_version'])} · {html.escape(report['chain_version'])}</span></footer></body></html>""".replace("证据", "相关信号").replace("evidence", "").replace("Evidence", "")


def report_markdown(report: dict[str, Any]) -> str:
    semantic = report.get("semantic_analysis") if report.get("semantic_status") == "complete" else None
    lines = ["# Codex 使用审计", "", f"> 本地私密报告 · {report['range']}", "", "## 三条结论", ""]
    for point in conclusion_points(report):
        lines.extend([f"### {point['number']} {point['question']}", "", point["answer"], "", f"- {point['support']}", ""])
    lines.extend(["## 接下来只做这三件事", ""])
    semantic_actions = semantic.get("actions", []) if semantic else []
    if semantic_actions:
        for index, item in enumerate(semantic_actions, 1):
            lines.extend([f"{index}. {item.get('behavior_change', '')}", f"   - 触发条件：{item.get('trigger_condition', '')}", f"   - 下次检查：{item.get('next_review_signal', '')}"])
    elif report["findings"]:
        for index, item in enumerate(report["findings"], 1):
            lines.extend([f"{index}. {item['behavior_change']}", f"   - 触发条件：{item['trigger']}", f"   - 下次检查：{item['check_signal']}"])
    else:
        lines.append("本周期没有足够高置信度的信息，未生成行动建议。")
    lines.extend(["", "## 主要投入", ""])
    if semantic:
        deep_by_id = {item.get("project_id"): item for item in semantic.get("projects", [])}
        for project in report.get("semantic_projects", []):
            lines.extend([f"### {project.get('name', '未命名项目')}", "", f"- 交互活动估算：{project.get('interaction_activity_estimate', 0)} 分钟", f"- 会话数：{len(project.get('member_thread_refs', []))}"])
            claims = deep_by_id.get(project.get("project_id"), {}).get("claims", [])
            for claim in claims:
                lines.append(f"- {claim.get('text', '')}")
            lines.append("")
    else:
        for item in report["workspaces"][:3]:
            lines.append(f"- {item['alias']}：{item['scenario']}，{item['event_count']} 个可定位事件")
    lines.extend(["", "## 数据概览", "", f"- 事件覆盖分钟数：{report['metrics']['event_coverage_minutes']}", f"- Codex 交互活动估算分钟数：{report['metrics']['interaction_interval_estimate_minutes']}", f"- 并行重叠分钟数：{report['metrics']['overlap_minutes']}", f"- 工作区：{report['workspace_count']}", f"- 可定位事件：{report['event_count']}", f"- 已确认产物事件：{report['confirmed_artifact_events']}", "", "## 场景分布", ""])
    lines.extend(f"- {name}：{count}" for name, count in sorted(report["scenario_distribution"].items(), key=lambda item: (-item[1], item[0])))
    if semantic:
        patterns = semantic.get("work_patterns", {})
        lines.extend(["", "## 工作模式", ""])
        for title, key in (("主要问题", "problems"), ("本周期进步", "progress"), ("稳定优势", "stable_strengths"), ("可复用工作流", "reusable_workflows")):
            lines.append(f"### {title}")
            values = patterns.get(key, [])
            lines.extend(f"- {item.get('text', '')}" for item in values)
            if not values:
                lines.append("- 信息不足，未生成该维度结论。")
            lines.append("")
    quality = report.get("data_quality", {})
    lines.extend(["", "## 口径", "", "- 时间指标是可观察 Codex 协作活动的估算，不等同真实工时。", "- 未显式标记的新任务可能保留在同一消息链中。", f"- JSONL 结构识别率：{quality.get('structural_parse_rate', 0) * 100:.1f}%（未知记录 {quality.get('unknown_records', 0)} 条）。", "- 详细审计信息保存在同目录的本地 manifest 文件中。", "", "## 分析版本", "", "```json", json.dumps(report.get("analysis_metadata", {}), ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines).replace("证据", "相关信号") + "\n"


def semantic_preview(events: list[Event]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.kind == "USER_MESSAGE":
            grouped[event.workspace].append(event)
    result = []
    for workspace in sorted(grouped)[:3]:
        for event in grouped[workspace][:20]:
            result.append({"workspace_alias": workspace, "time": event.timestamp.isoformat(), "request_summary": redact(normalize(event.text), 240), "scenario": scenario([event])[0], "confidence": scenario([event])[1]})
    return result[:20]


def report_dimensions(events: list[Event], workspace_events: dict[str, list[Event]]) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]]]:
    anchors = [event for event in events if event.kind in {"USER_MESSAGE", "AGENT_MESSAGE", "ASSISTANT_MESSAGE_FALLBACK"}]
    daily_minutes: dict[str, set[tuple[int, int]]] = defaultdict(set)
    hourly_minutes: dict[int, set[tuple[str, int]]] = defaultdict(set)
    for event in anchors:
        local = event.timestamp.astimezone()
        daily_minutes[local.date().isoformat()].add((local.hour, local.minute))
        hourly_minutes[local.hour].add((local.date().isoformat(), local.minute))
    daily = {day: len(minutes) for day, minutes in sorted(daily_minutes.items())}
    hourly = {hour: len(hourly_minutes[hour]) for hour in range(24)}
    summaries = []
    for alias, items in workspace_events.items():
        label, confidence = scenario(items)
        artifacts = sum(event.kind in {"PATCH_RESULT", "IMAGE_RESULT"} and bool(event.paths) for event in items)
        summaries.append({"alias": alias, "scenario": label, "confidence": confidence, "time_range": f"{min(event.timestamp for event in items).astimezone().date()} 至 {max(event.timestamp for event in items).astimezone().date()}", "confirmed_artifact_events": artifacts, "event_count": len(items)})
    return daily, hourly, sorted(summaries, key=lambda item: (-item["event_count"], item["alias"]))


def run(input_root: Path, output_dir: Path, start: dt.datetime, end: dt.datetime, preview: bool = False) -> dict[str, Path]:
    gaps: Counter[str] = Counter()
    all_events = load_events(input_root, gaps)
    assign_aliases(all_events)
    assign_chains(all_events)
    events = selected(all_events, start, end)
    metrics = interval_metrics(events)
    report_id = f"codex-audit-{end.astimezone(LOCAL_TZ).date().isoformat()}-{sha(f'{start.isoformat()}|{end.isoformat()}', 8)}"
    workspace_events: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        workspace_events[event.workspace].append(event)
    scenario_distribution = Counter(scenario(items)[0] for items in workspace_events.values())
    artifacts = [event for event in events if event.kind in {"PATCH_RESULT", "IMAGE_RESULT"} and event.paths]
    daily, hourly, workspaces = report_dimensions(events, workspace_events)
    total_records = gaps.get("_records_total", 0)
    recognized_records = gaps.get("_records_adapted", 0) + gaps.get("_records_known_ignored", 0)
    public_gaps = {key: value for key, value in sorted(gaps.items()) if not key.startswith("_")}
    report = {
        "report_id": report_id,
        "range": f"{start.astimezone(LOCAL_TZ).date().isoformat()} 至 {end.astimezone(LOCAL_TZ).date().isoformat()}",
        "privacy_label": "本地私密报告",
        "adapter_version": ADAPTER_VERSION,
        "chain_version": CHAIN_VERSION,
        "analysis_metadata": {
            "model_id": None,
            "analysis_prompt_version": ANALYSIS_PROMPT_VERSION,
            "semantic_input_schema_version": SEMANTIC_INPUT_SCHEMA_VERSION,
            "semantic_output_schema_version": SEMANTIC_OUTPUT_SCHEMA_VERSION,
            "scenario_classifier_version": SCENARIO_CLASSIFIER_VERSION,
            "action_rules_version": ACTION_RULES_VERSION,
        },
        "metrics": metrics,
        "workspace_count": len(workspace_events),
        "event_count": len(events),
        "confirmed_artifact_events": len(artifacts),
        "daily_activity": daily,
        "hourly_activity": hourly,
        "scenario_distribution": dict(sorted(scenario_distribution.items())),
        "workspaces": workspaces,
        "data_quality": {"total_records": total_records, "adapted_records": gaps.get("_records_adapted", 0), "known_ignored_records": gaps.get("_records_known_ignored", 0), "recognized_records": recognized_records, "unknown_records": gaps.get("_records_unknown", 0), "structural_parse_rate": round(recognized_records / total_records, 4) if total_records else 1.0},
        "data_gaps": public_gaps,
        "findings": action_findings(events),
    }
    secure_directory(output_dir)
    paths = {"json": output_dir / f"{report_id}.json", "markdown": output_dir / f"{report_id}.md", "html": output_dir / f"{report_id}.html", "manifest": output_dir / f"{report_id}.audit-manifest.json"}
    secure_write_text(paths["json"], json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    secure_write_text(paths["markdown"], report_markdown(report))
    secure_write_text(paths["html"], report_html(report))
    secure_write_text(paths["manifest"], json.dumps(manifest(events), ensure_ascii=False, indent=2) + "\n")
    if preview:
        semantic = output_dir / f"{report_id}.semantic-preview.json"
        secure_write_text(semantic, json.dumps(semantic_preview(events), ensure_ascii=False, indent=2) + "\n")
        paths["semantic_preview"] = semantic
    return paths


def secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def secure_write_text(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local Codex usage audit from JSONL.")
    range_group = parser.add_mutually_exclusive_group()
    range_group.add_argument("--days", type=int, default=7)
    range_group.add_argument("--from", dest="date_from", help="Start date, YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="End date, YYYY-MM-DD; required with --from")
    parser.add_argument("--input-root", type=Path, default=Path.home() / ".codex", help="Read-only JSONL root")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".codex" / "reports")
    parser.add_argument("--semantic-preview", action="store_true")
    args = parser.parse_args()
    now = dt.datetime.now(tz=LOCAL_TZ)
    if args.date_from:
        if not args.date_to:
            parser.error("--to is required with --from")
        try:
            start = dt.datetime.fromisoformat(args.date_from).replace(tzinfo=LOCAL_TZ)
            end = dt.datetime.fromisoformat(args.date_to).replace(tzinfo=LOCAL_TZ) + dt.timedelta(days=1) - dt.timedelta(microseconds=1)
        except ValueError:
            parser.error("dates must be YYYY-MM-DD")
        if (end - start).days > 90 or end < start:
            parser.error("custom ranges must be between 0 and 90 days")
    else:
        if not 1 <= args.days <= 90:
            parser.error("--days must be between 1 and 90")
        end = now
        first_date = now.date() - dt.timedelta(days=args.days - 1)
        start = dt.datetime.combine(first_date, dt.time.min, tzinfo=LOCAL_TZ)
    paths = run(args.input_root, args.output_dir, start, end, args.semantic_preview)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
