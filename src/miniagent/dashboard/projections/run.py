from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from miniagent.dashboard.config import LIMITS
from miniagent.dashboard.cursors import PageBoundary
from miniagent.dashboard.reads import StoredEvent, StoredRun
from miniagent.dashboard.redaction import redact_text
from miniagent.dashboard.views import FieldView, RunView, SourceTarget
from miniagent.metering.usage import RunUsage
from miniagent.runtime.events import CompactionCompleted, EvaluationCompleted
from miniagent.runtime.state import TERMINAL_STATUSES
from miniagent.storage.codecs import encode_datetime


def format_usd(value: Decimal) -> str:
    if not value.is_finite():
        return "不可计算"
    if value.adjusted() >= LIMITS.scalar_bytes - 8:
        return "超出展示上限"
    with localcontext() as context:
        context.prec = max(28, value.adjusted() + 8)
        return format(
            value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f"
        )


def _display(value: object, *, limit: int = LIMITS.scalar_bytes) -> str:
    if value is None:
        return "未记录"
    text = redact_text(
        encode_datetime(value) if isinstance(value, datetime) else str(value)
    )
    content = text.encode("utf-8")
    if len(content) <= limit:
        return text
    suffix = "…（已截短）"
    return (
        content[: limit - len(suffix.encode())].decode("utf-8", errors="ignore")
        + suffix
    )


def project_run(
    run: StoredRun,
    boundary: PageBoundary,
    *,
    latest_compaction: CompactionCompleted | None = None,
    evaluation_event: StoredEvent | None = None,
) -> RunView:
    envelope = run.envelope
    missing = {
        issue.field_path for issue in run.issues if issue.code == "field_not_recorded"
    }

    def field(
        key: str,
        label: str,
        value: object,
        *,
        path: tuple[str | int, ...] = (),
        source_label: str = "Run",
        limit: int = LIMITS.scalar_bytes,
    ) -> FieldView:
        return FieldView(
            key,
            label,
            "未记录" if path in missing else _display(value, limit=limit),
            source_label,
        )

    identity = [
        field("run_id", "Run ID", envelope.run_id),
        field("created_at", "创建时间", envelope.created_at),
        field("updated_at", "最后提交时间", envelope.updated_at),
        field("finished_at", "结束时间", envelope.finished_at),
        field("workspace_kind", "Workspace 类型", run.workspace_kind),
    ]
    result = [
        field("evaluator_type", "验证类型", "该 Run 验证类型未记录，未证明独立验证"),
        field("artifacts", "Artifact 可用性", "尚未检查，Workspace查看"),
    ]
    if run.workspace_kind == "local":
        result.append(
            field(
                "default_local_evaluator",
                "当前默认装配说明",
                "当前默认 Local 装配使用 pass-through；不代表该 Run 的验证类型",
            )
        )
    if (
        evaluation_event is not None
        and evaluation_event.event is not None
        and isinstance(evaluation_event.event.payload, EvaluationCompleted)
    ):
        result.append(
            FieldView(
                "evaluation",
                "最后记录的评测结果",
                "true" if evaluation_event.event.payload.passed else "false",
                f"Event #{evaluation_event.sequence}",
                SourceTarget(envelope.run_id, event_id=evaluation_event.event_id),
            )
        )
    else:
        result.append(
            field(
                "evaluation",
                "最后记录的评测结果",
                "未完成读取" if evaluation_event is not None else "未记录",
            )
        )
    budgets = []
    usage = []
    if run.record is None:
        identity.extend(
            field(key, label, "未完成读取")
            for key, label in (
                ("task", "任务"),
                ("model", "模型"),
                ("started_at", "开始时间"),
            )
        )
        result.extend(
            field(key, label, "未完成读取")
            for key, label in (
                ("termination_reason", "终止原因"),
                ("candidate_output", "候选输出"),
                ("final_output", "最终输出"),
            )
        )
        usage.extend(field(key, key, "未完成读取") for key in RunUsage.model_fields)
        budgets.extend(
            field(key, key, "未完成读取")
            for key in (
                "steps_started",
                "max_steps",
                "consecutive_failed_steps",
                "max_consecutive_failures",
                "context_window_tokens",
                "max_output_tokens",
            )
        )
    else:
        state = run.record.state
        identity.extend(
            (
                field("task", "任务", state.identity.task, limit=LIMITS.state_bytes),
                field("model", "模型", state.definition.agent_spec.model.model),
                field("started_at", "开始时间", state.lifecycle.started_at),
            )
        )
        result.extend(
            (
                field(
                    "termination_reason", "终止原因", state.lifecycle.termination_reason
                ),
                field(
                    "error_code",
                    "错误代码",
                    state.lifecycle.error.code if state.lifecycle.error else None,
                ),
                field(
                    "error_message",
                    "错误消息",
                    state.lifecycle.error.message if state.lifecycle.error else None,
                ),
                field("candidate_output", "候选输出", state.lifecycle.candidate_output),
                field("final_output", "最终输出", state.lifecycle.final_output),
            )
        )
        for key in RunUsage.model_fields:
            if (
                key == "cached_input_cost_usd"
                and not state.usage.cached_input_priced_separately
            ):
                continue
            value = getattr(state.usage, key)
            usage.append(
                field(
                    key,
                    "累计模型成本（USD）" if key == "cost_usd" else key,
                    format_usd(value) if isinstance(value, Decimal) else value,
                    path=("usage", key),
                )
            )
        usage.append(
            field(
                "cost_recorded_decimal",
                "原始已记录 Decimal",
                state.usage.cost_usd,
                path=("usage", "cost_usd"),
            )
        )
        started = state.lifecycle.started_at
        wall = "未记录"
        if started is not None:
            elapsed = (state.lifecycle.finished_at or boundary.read_at) - started
            wall = (
                "不可计算"
                if elapsed.total_seconds() < 0
                else str(
                    (elapsed.days * 86400 + elapsed.seconds) * 1000
                    + elapsed.microseconds // 1000
                )
            )
        usage.append(
            field("wall_duration_ms", "墙钟经过时间（非终态不证明进程存活）", wall)
        )
        for counter, maximum in (
            ("steps_started", "max_steps"),
            ("consecutive_failed_steps", "max_consecutive_failures"),
        ):
            value = getattr(state.execution, counter)
            cap = getattr(state.definition.config, maximum)
            display = str(value)
            if ("definition", "config", maximum) not in missing and (
                "execution",
                counter,
            ) not in missing:
                display += f" ({Decimal(value) * 100 / Decimal(cap):.0f}%)"
            budgets.extend(
                (
                    field(counter, counter, display, path=("execution", counter)),
                    field(
                        maximum, maximum, cap, path=("definition", "config", maximum)
                    ),
                )
            )
        budgets.extend(
            (
                field(
                    "context_window_tokens",
                    "模型上下文容量",
                    state.definition.config.context_window_tokens,
                    path=("definition", "config", "context_window_tokens"),
                ),
                field(
                    "max_output_tokens",
                    "输出预留",
                    state.definition.agent_spec.generation.max_output_tokens
                    if state.definition.agent_spec.generation.max_output_tokens
                    is not None
                    else "未配置",
                    path=(
                        "definition",
                        "agent_spec",
                        "generation",
                        "max_output_tokens",
                    ),
                ),
            )
        )
        if latest_compaction is not None:
            usage.extend(
                (
                    field(
                        "old_input_tokens",
                        "最近压缩前 input 估计",
                        latest_compaction.old_input_tokens,
                        source_label="CompactionCompleted",
                    ),
                    field(
                        "new_input_tokens",
                        "最近压缩后 input 估计",
                        latest_compaction.new_input_tokens,
                        source_label="CompactionCompleted",
                    ),
                )
            )
    usage.append(field("cost_completeness", "计费说明", "已记录费用，完整性未证明"))
    budgets.extend(
        field(key, label, "未配置")
        for key, label in (
            ("time_limit", "时间硬上限"),
            ("token_limit", "累计 Token 硬上限"),
            ("cost_limit", "费用硬上限"),
        )
    )
    return RunView(
        envelope.run_id,
        envelope.status.value,
        envelope.status in TERMINAL_STATUSES,
        tuple(identity),
        tuple(result),
        tuple(usage),
        tuple(budgets),
        run.issues,
    )
