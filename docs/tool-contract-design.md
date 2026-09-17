# Tool Registry、输入校验、输出截断与结构化错误

状态：已实现。基线：2026-09-17 当前代码。本文记录当前执行链采用的 Tool 契约及其边界。

## 1. 现状和范围

| 能力 | 原有实现 | 当前实现 |
| --- | --- | --- |
| Registry | CLI 返回 `dict[str, Tool]`；Runtime 检查名称和 Agent 白名单 | 统一注册、定义快照、严格参数校验入口 |
| 输入 Schema | 两个工具手写 Schema；Provider 只解析 JSON object | 用参数模型统一生成 Schema 和执行校验 |
| 输出截断 | Local/Docker 分别限制 stdout/stderr，默认各 1 MiB | 保留采集截断事实；限制所有工具发送给模型的消息 |
| 结构化错误 | `ToolError(code, message, retryable)` 已存在 | 参数错误明细、结果不变量、完整的模型错误反馈 |

对应代码：[`models/tool.py`](../src/miniagent/models/tool.py)、[`tools/`](../src/miniagent/tools/)、[`runtime/loop.py`](../src/miniagent/runtime/loop.py)、[`environments/`](../src/miniagent/environments/)。

本方案保留 `read_file`、`shell` 的名称和当前串行调用顺序。不引入动态插件、MCP、Policy Engine、自动重试或持久化工具事件；这些能力可在后续接入。Registry 的存在不增强 Local Workspace 的安全隔离，也不限制 Shell 命令本身。

## 2. 职责和调用顺序

| 所属层 | 职责 |
| --- | --- |
| CLI bootstrap | 实例化绑定 Workspace 的工具，构建 Registry，注入 Runtime |
| Tool Registry | 注册工具、提供定义、检查 Agent 工具配置、验证调用参数 |
| Runtime | 白名单检查、执行排序、取消、异常翻译、结果契约检查、构造工具消息 |
| Tool | 参数的业务语义检查、调用 Workspace、将执行结果映射为 Tool Result |
| Workspace | 执行命令、进程超时和取消、流式采集上限 |

调用流程：

1. 构造 Registry，验证注册信息；Run 开始前验证 Agent 的 `allowed_tools`。
2. 根据 `allowed_tools` 顺序提供 ToolDefinition 给 Provider。
3. 收到 ToolCall 后先检查取消，再检查白名单；未授权调用返回 `TOOL_NOT_ALLOWED`。
4. Registry 用该工具的参数模型严格校验 `arguments`。失败时生成 `INVALID_TOOL_ARGUMENTS`，不执行工具。
5. Runtime 通过已有 cancellation wrapper 调用 `Tool.execute()`。
6. 检查 ToolResult 的关联 ID、状态和错误不变量；捕获普通执行异常并翻译为工具错误。
7. 将结果投影为有大小上限的 JSON 消息，追加到 `Message(role="tool")`。
8. 继续使用现有 `tool_step_failed()`：本 Step 所有结果均非 success 才计一次连续失败。

取消继续沿现有取消路径传播，不包装为 ToolError，不生成虚假的成功结果。`retryable` 不触发 Registry 或 Runtime 自动重试。

## 3. Registry 与参数模型

### 3.1 单一参数定义来源

采用现有 Pydantic 依赖，每个工具提供 `arguments_model`。接口如下：

```python
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from miniagent.models.tool import ToolDefinition, ToolResult


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReadFileArguments(ToolArguments):
    path: str = Field(min_length=1, description="Path relative to workspace root.")


class ShellArguments(ToolArguments):
    command: str = Field(min_length=1)


class Tool(ABC):
    arguments_model: type[ToolArguments]

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition: ...

    @abstractmethod
    async def execute(
        self, arguments: dict[str, Any], *, tool_call_id: str
    ) -> ToolResult: ...
```

工具 definition 的 `input_schema` 必须由 `arguments_model.model_json_schema()` 生成，删除两份手写 Schema。Registry 用 `model_validate(arguments, strict=True)` 验证，然后 `model_dump(mode="python")` 得到执行参数；默认值在这里补齐。首批参数只使用 JSON 可表达的字段，后续复杂类型必须验证其 Schema 与运行期语义是否一致。

此方案暂不支持导入任意外部 JSON Schema。未来如需接受 MCP 等外部 Schema，再评估独立 JSON Schema validator；目前不增加依赖，也不手写部分 JSON Schema 解释器。Pydantic 自定义业务验证不一定体现在生成的 Schema 中，因此 Schema 是模型的输入说明，执行期校验始终有效。

路径逃逸、symlink、Workspace 状态和命令策略属于业务或环境检查；参数类型校验通过不表示获准访问。`read_file` 现有路径拒绝逻辑保留，symlink 边界另按 Workspace 能力处理。

### 3.2 Registry 接口

`src/miniagent/tools/registry.py` 只在构造时注册，Run 中不支持增删：

```python
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from miniagent.models.tool import ToolCall, ToolDefinition, ToolError
from miniagent.tools import Tool


@dataclass(frozen=True)
class PreparedToolCall:
    tool: Tool
    arguments: dict[str, Any]


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None: ...

    def definitions(self, allowed_tools: Sequence[str]) -> list[ToolDefinition]: ...

    def prepare(self, call: ToolCall) -> PreparedToolCall | ToolError: ...
```

- 用 `definition.name` 作为唯一键；Iterable 可以发现重复名称，避免字典覆盖后才注册。
- 注册时拒绝重复名称、空描述、无参数模型以及 definition Schema 与参数模型生成结果不一致。名称限定 `^[A-Za-z0-9_-]{1,64}$`，这是项目约束。
- 保存定义的深拷贝；`definitions()` 返回深拷贝，调用者修改 Schema 不影响注册内容。工具的参数模型在注册后固定，禁止热更新。
- `definitions()` 拒绝未知或重复的 Agent 工具名称，保留配置顺序。此类配置错误在请求模型前 fail fast，并提供工具名。
- `prepare()` 只做查找和参数校验，不持有 Agent、Workspace 或 cancellation token。正常 Runtime 路径必须先通过白名单检查；未知名称返回 `TOOL_NOT_FOUND` 供独立调用者使用。
- Registry 不提供第二套执行循环；PreparedToolCall 只在当前调用中使用，不进入 Run State。

## 4. 结构化结果与错误

保留 `ToolResult.content`、`data`、`artifacts`、`metrics`、`workspace_delta`，避免引入 `summary`、`artifact_refs` 的并行命名。新增字段如下，均使用 Pydantic BaseModel：

```python
from pydantic import BaseModel, Field


class ToolArgumentIssue(BaseModel):
    location: list[str | int] = Field(default_factory=list)
    code: str
    message: str


class ToolError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    issues: list[ToolArgumentIssue] = Field(default_factory=list)


class ToolOutputInfo(BaseModel):
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    content_truncated: bool = False
    omitted_fields: list[str] = Field(default_factory=list)
```

`ToolResult` 增加 `output: ToolOutputInfo = Field(default_factory=ToolOutputInfo)`。前两个标志来自 Workspace；后两个描述模型消息投影，投影在副本上进行，不修改原始结果。`omitted_fields` 使用受控字段路径，如 `data`、`artifacts`、`error.issues`，不用任意输出内容作为字段名。

结果不变量：`SUCCESS` 必须没有 error；`ERROR`、`TIMEOUT`、`DENIED` 必须有 error；`tool_call_id` 必须与当前调用一致。ToolResult 的模型验证负责状态不变量，Runtime 负责关联 ID 检查。结果类型不正确或关联不正确返回 `INVALID_TOOL_RESULT`；执行中抛出的普通异常，包括工具构造无效结果时抛出的验证异常，返回 `TOOL_EXECUTION_EXCEPTION`。

| 错误 | status | retryable | 处理含义 |
| --- | --- | --- | --- |
| `TOOL_NOT_ALLOWED` | denied | false | Agent 无权调用 |
| `TOOL_NOT_FOUND` | error | false | Registry 找不到名称 |
| `INVALID_TOOL_ARGUMENTS` | error | false | 修改参数后发起新调用 |
| `PATH_OUTSIDE_WORKSPACE` | denied | false | 保留现有路径拒绝语义 |
| `FILE_NOT_FOUND` / `DECODE_ERROR` / `READ_FAILED` | error | false | 保留现有工具错误码 |
| `OUTPUT_TRUNCATED` | error | false | `read_file` 未能履行完整读取契约 |
| `COMMAND_FAILED` | error | false | 命令执行失败 |
| `COMMAND_TIMEOUT` | timeout | false | 当前 Workspace 可能已 BROKEN，不能直接重试 |
| `TOOL_EXECUTION_EXCEPTION` / `INVALID_TOOL_RESULT` | error | false | 执行或实现契约出错 |

`retryable=true` 仅表示在当前环境和参数下可安全重试的已知瞬时失败；不表示错误可通过改参数修复，也不保证重试成功。当前 Shell timeout 的 `retryable=True` 应改为 false，除非后续能证明 Workspace 仍可用且重复执行安全。命令超时前可能已经产生副作用。

参数错误最多保留 8 个 issue，location 表示字段路径；类型、必填、额外字段等 code 从 Pydantic 错误类型归一化。面向模型的 message 使用受控短文本，不原样包含验证器的 input/context、完整参数或异常堆栈。未知执行异常的详细信息写诊断日志，模型只收到稳定错误码和简短说明。

### 模型消息

Runtime 将投影后的结果用 `orjson` 序列化一次，作为现有 `Message.content` 字符串；Provider 继续原样传递该字符串，无须改变消息协议。例如：

```json
{
  "tool_call_id": "call_1",
  "status": "error",
  "content": "Invalid arguments for read_file.",
  "error": {
    "code": "INVALID_TOOL_ARGUMENTS",
    "message": "Tool arguments did not pass validation.",
    "retryable": false,
    "issues": [{"location": ["path"], "code": "missing", "message": "Field is required."}]
  },
  "output": {
    "stdout_truncated": false,
    "stderr_truncated": false,
    "content_truncated": false,
    "omitted_fields": []
  }
}
```

空的可选 data/artifacts/metrics/workspace_delta 可省略。运行结果中的 error 和模型消息中的 error 必须表达同一个错误，不能只发 `content`。这一步不等于持久化：当前 RunResult 不保存全部工具结果，事件存储仍是后续工作。

## 5. 两层输出上限

### 5.1 Workspace 采集

保留默认 stdout、stderr 各 1 MiB 的原始字节上限和继续排空管道的实现。命中采集上限时设置对应标志，UTF-8 解码沿用替换不完整尾部字节的行为。上限只约束采集器保留的输出，不限制子进程内部内存；当前 `read_file` 子进程仍会一次读入文件。

`shell` 把两个标志复制到 ToolResult.output，命令 exit code 为 0 时保持 success。`read_file` 继续返回 `OUTPUT_TRUNCATED` 和 error，因为目前它承诺返回完整文件。未来新增窗口读取时，所请求窗口完整返回可以 success，但不能静默改变现有含义。

### 5.2 模型消息投影

`ToolOutputLimits(BaseModel)` 挂在 `RuntimeConfig.tool_output_limits`，默认值为：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `max_content_bytes` | 16 KiB | 工具 content 文本 UTF-8 上限 |
| `max_message_bytes` | 32 KiB | 最终序列化 JSON 的总字节上限 |

两者均为正数，`max_message_bytes >= 2048` 且 `max_content_bytes <= max_message_bytes`。这两个限制不替代 Workspace 上限，也不等同于 token budget 或整个对话的 context compaction。

纯投影函数 `render_tool_result(result, limits) -> str` 按以下步骤处理：

1. 构造模型消息副本，对 content 按 UTF-8 完整字符边界保留前缀，记录 `content_truncated`；不向 JSON 字节串直接追加截断标记。
2. 序列化后按实际字节数测量，涵盖 JSON 转义的额外开销。超限时依次省略 `data`、`metrics`、`workspace_delta`、`artifacts`、`error.issues`，在 `omitted_fields` 记录真正移除的非空字段。
3. 仍超限时缩短 content，然后缩短 error.message，并记录相应截断/省略事实。每次都重新序列化测量；保留 status、error.code、retryable 和 output。
4. 以空 content 和空 error.message 为最小候选，确认可容纳后，使用有界搜索选择可容纳的文本前缀。不得发送非法或超限 JSON。

为保证最小候选有界，ToolCall ID 限制为非空且最多 256 UTF-8 字节，错误码限制为 `^[A-Z0-9_]{1,64}$`；这些是新增项目协议约束，Provider 和外部调用者需同步验证。不符合 ID 约束的模型响应在 Provider 边界作为协议错误终止，不能截断 ID 后继续关联。Runtime 检查返回结果的错误码，不合约束时按 `INVALID_TOOL_RESULT` 翻译。受控的 output 字段集合保证最小候选可放入 2048 字节。

投影不会把成功结果改为失败，也不会覆盖已有错误。例如命令失败且 stderr 很长时，同时保留 `COMMAND_FAILED` 与 `stderr_truncated=true`。没有执行过命令的参数错误，两个流标志均为 false。

采集时丢弃的字节无法通过 artifact 恢复，本阶段不承诺保存完整输出。已有 artifacts 仍可携带；被消息预算省略的引用只是不发送给模型。后续若要求完整输出，需要在丢弃前将流写入受限存储，并定义 artifact 大小上限及存储失败语义；不能用当前已截断文本冒充完整输出。

## 6. Provider 参数解析边界

本轮保留 `ToolCall.arguments: dict[str, Any]`：非法 JSON 或顶层不是 object，仍由 Provider 抛出解析异常，Runtime 翻译为当前 Run 级 `PROVIDER_EXCEPTION`，不是 `INVALID_TOOL_ARGUMENTS`。因此一个响应中含有解析失败的调用时，当前整个响应失败，不执行其中其他调用。

合法 JSON object 的字段错误才进入 Registry。将非法 JSON 也变成可反馈模型的逐调用错误，需要新增原始参数与解析失败表示，并更新 assistant 历史消息重放；这是独立协议扩展。本阶段不把非法 JSON 替换成 `{}`，避免伪造模型请求。

## 7. 落地结果与兼容性

1. `models/tool.py` 已包含错误明细、输出信息、大小配置和结果不变量；两个工具使用参数模型生成 Schema。
2. CLI 和 Runtime 已统一使用 `ToolRegistry`。直接 `Tool.execute()` 是内部低层入口，调用者自负参数合法性；模型调用统一经过 Registry。
3. Workspace 截断标志已传到 ToolResult；timeout 不再声明可重试；Runtime 使用纯投影函数发送有界 JSON 工具消息。
4. Registry、输入校验、结构化结果、消息截断和 Workspace 输出行为均有契约测试覆盖，repository map 已标记为 wired。

这是一次明确的内部接口迁移，不长期同时支持 Mapping 和 Registry。现有 `Tool.execute(arguments, tool_call_id=...)` 参数形式、工具名称、成功/失败计数和取消语义保持一致。新增 ID 约束也影响使用 ToolCall 的 snapshot 合约及其测试，需同批更新；当前尚未接入持久化，未来接入 snapshots 时再为历史工具消息定义版本兼容。

## 8. 验收场景

| 场景 | 期望 |
| --- | --- |
| 重复注册、无效定义、未知 Agent 工具 | 请求模型前明确失败 |
| 修改返回的 definition Schema | 不影响 Registry 后续校验 |
| 缺 path、path 为整数、额外字段、空字符串 | `INVALID_TOOL_ARGUMENTS`；Workspace 未执行 |
| 模型调用白名单外工具 | denied；不校验或执行该工具 |
| 合法参数和默认值 | 生成 Schema 与实际校验相符；参数正确传给工具 |
| 工具抛异常或返回不匹配 ID | 明确结构化错误；没有未关联的工具消息 |
| 工具执行中取消 | 取消传播；不会被转为普通 error |
| 命令成功但 stdout 截断 | success 与 stdout_truncated 同时存在 |
| 命令失败且 stderr 截断 | 保留原错误与截断标志 |
| 完整文件读取被截断 | 保留现有 `OUTPUT_TRUNCATED` 契约 |
| 超大自定义 content/data/error/artifacts | 最终消息合法 JSON 且不超过总字节上限 |
| 中文、emoji、引号、反斜杠、恰好达到上限 | 字符边界正确；序列化后大小满足约束 |
| QueueProvider → Runtime → Tool → 下一次请求 | 模型看见 status、error.code、retryable、issues 和 output |
| 同 Step 一成功一失败；连续全失败 | 保留现有 Step 失败计数逻辑 |
| 非法 JSON 或非 object 参数 | 明确维持 Provider 级失败边界 |

测试使用真实 Registry、参数模型和投影函数，仅在 Provider/Workspace 外部边界使用 fake，并验证失败参数没有执行副作用。
