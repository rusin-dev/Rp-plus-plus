from __future__ import annotations

import json
import threading
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from anthropic import Anthropic
from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from ..config import Config
from ..core.event_bus import Event, EventBus, EventTypes
from ..core.logger import get_logger
from .tools import ToolRegistry


def client_credentials(config: type[Config]) -> tuple[str, str]:
    """返回当前活动 provider 的凭证。"""
    provider = config.active_provider()
    if provider is None:
        return "", ""
    return provider.api_key or "", provider.api_url or ""


def make_client(config: type[Config]) -> OpenAI | Anthropic:
    """创建与当前 provider 匹配的客户端。

    openai / responses → OpenAI SDK（前者用 chat.completions，后者用 responses）；
    anthropic → 官方 anthropic SDK。
    """
    api_key, base_url = client_credentials(config)
    provider = config.active_provider()
    if provider is not None and provider.type == "anthropic":
        return Anthropic(api_key=api_key)
    return OpenAI(api_key=api_key, base_url=base_url)


def _to_anthropic_tools(schemas: Any) -> list[dict]:
    """OpenAI 工具 schema → Anthropic tools 格式。"""
    out: list[dict] = []
    for schema in schemas or []:
        if not isinstance(schema, dict) or schema.get("type") != "function":
            continue
        func = schema.get("function") or {}
        out.append(
            {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _to_responses_tools(schemas: Any) -> list[dict]:
    """OpenAI 工具 schema → Responses API tools 格式。"""
    out: list[dict] = []
    for schema in schemas or []:
        if not isinstance(schema, dict) or schema.get("type") != "function":
            continue
        func = schema.get("function") or {}
        out.append(
            {
                "type": "function",
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "parameters": func.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return out


def _split_system(
    messages: list[ChatCompletionMessageParam],
) -> tuple[str, list[ChatCompletionMessageParam]]:
    """从 OpenAI chat 消息中抽出 system 内容。"""
    parts: list[str] = []
    rest: list[ChatCompletionMessageParam] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            content = m.get("content")
            if isinstance(content, str) and content:
                parts.append(content)
        else:
            rest.append(m)
    return "\n\n".join(parts), rest


def _to_anthropic_messages(chat_messages: list[ChatCompletionMessageParam]) -> list[dict]:
    """OpenAI chat 消息 → Anthropic messages 格式。

    工具结果按 Anthropic 约定折叠为 role="user" 的 tool_result 块。
    """
    out: list[dict] = []
    for m in chat_messages:
        role = m.get("role")
        if role == "user":
            content = m.get("content", "")
            out.append({"role": "user", "content": content})
        elif role == "assistant":
            text = m.get("content")
            tool_calls = m.get("tool_calls") or []
            blocks: list[dict] = []
            if isinstance(text, str) and text:
                blocks.append({"type": "text", "text": text})
            for tc in tool_calls:
                func = tc.get("function") or {}
                raw_args = func.get("arguments") or ""
                try:
                    input_obj = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    input_obj = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": input_obj,
                    }
                )
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", ""),
                        }
                    ],
                }
            )
    return out


def _to_responses_input(chat_messages: list[ChatCompletionMessageParam]) -> list[dict]:
    """OpenAI chat 消息 → Responses API input items。

    工具结果转为 function_call_output 项。
    """
    out: list[dict] = []
    for m in chat_messages:
        role = m.get("role")
        if role == "user":
            out.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": m.get("content", "")}],
                }
            )
        elif role == "assistant":
            text = m.get("content")
            if isinstance(text, str) and text:
                out.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            tool_calls = m.get("tool_calls") or []
            for tc in tool_calls:
                func = tc.get("function") or {}
                out.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments") or "",
                    }
                )
        elif role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": m.get("content", ""),
                }
            )
    return out


def _normalize_usage(
    *, input_tokens: int, output_tokens: int, total_tokens: int | None = None
) -> SimpleNamespace:
    """把各后端的 usage 归一化为 prompt_tokens/completion_tokens/total_tokens。"""
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _publish_token(bus: EventBus, agent_id: str | None, text: str) -> None:
    if agent_id is None:
        bus.publish(Event(EventTypes.TOKEN, text))
    else:
        bus.publish(Event(EventTypes.SUBAGENT_TOKEN, {"agent_id": agent_id, "text": text}))


def _publish_tool_call(bus: EventBus, agent_id: str | None, name: str, arguments: str) -> None:
    if agent_id is None:
        bus.publish(Event(EventTypes.TOOL_CALL, {"name": name, "arguments": arguments}))
    else:
        bus.publish(
            Event(
                EventTypes.SUBAGENT_TOOL_CALL,
                {"agent_id": agent_id, "name": name, "arguments": arguments},
            )
        )


def _publish_tool_result(bus: EventBus, agent_id: str | None, result: str) -> None:
    if agent_id is None:
        bus.publish(Event(EventTypes.TOOL_RESULT, result))
    else:
        bus.publish(
            Event(
                EventTypes.SUBAGENT_TOOL_RESULT,
                {"agent_id": agent_id, "result": result},
            )
        )


def _stream_openai_chat(
    config: type[Config],
    bus: EventBus,
    client: OpenAI,
    tools: ToolRegistry,
    messages: list[ChatCompletionMessageParam],
    mode: str,
    agent_id: str | None,
    record_usage: Callable[[object], None] | None,
    cancel_event: threading.Event | None,
) -> tuple[str, dict[int, dict[str, str]], bool]:
    """OpenAI chat.completions 流式（旧版默认后端）。"""
    extra_body = config.active_variant_params()
    stream = client.chat.completions.create(
        model=config.active_model(),
        messages=messages,
        tools=tools.schemas_for_mode(mode),
        stream=True,
        extra_body=extra_body or None,
        stream_options={"include_usage": True},
    )

    text = ""
    tool_calls: dict[int, dict[str, str]] = {}
    cancelled = False
    for chunk in stream:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        usage = getattr(chunk, "usage", None)
        if usage is not None and record_usage is not None:
            record_usage(usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text += delta.content
            _publish_token(bus, agent_id, delta.content)
        for tc in delta.tool_calls or []:
            slot = tool_calls.setdefault(
                tc.index,
                {"id": tc.id or "", "name": "", "arguments": ""},
            )
            if tc.id:
                slot["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

    if cancelled:
        try:
            stream.close()
        except Exception:
            pass
    return text, tool_calls, cancelled


def _stream_responses(
    config: type[Config],
    bus: EventBus,
    client: OpenAI,
    tools: ToolRegistry,
    messages: list[ChatCompletionMessageParam],
    mode: str,
    agent_id: str | None,
    record_usage: Callable[[object], None] | None,
    cancel_event: threading.Event | None,
) -> tuple[str, dict[int, dict[str, str]], bool]:
    """OpenAI Responses API 流式后端。"""
    system_text, chat = _split_system(messages)
    input_items = _to_responses_input(chat)
    extra_body = config.active_variant_params()

    create_kwargs: dict[str, Any] = {
        "model": config.active_model(),
        "input": input_items,
        "tools": _to_responses_tools(tools.schemas_for_mode(mode)),
        "stream": True,
    }
    if system_text:
        create_kwargs["instructions"] = system_text
    if extra_body:
        create_kwargs["extra_body"] = extra_body

    stream = client.responses.create(**create_kwargs)

    text = ""
    # 按 item_id 累积工具调用的参数 JSON
    args_buf: dict[str, str] = {}
    finalized_calls: dict[str, dict[str, str]] = {}
    cancelled = False
    input_tokens = 0
    output_tokens = 0

    for event in stream:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        etype = getattr(event, "type", "")
        if etype == "response.output_text.delta":
            piece = getattr(event, "delta", "")
            if piece:
                text += piece
                _publish_token(bus, agent_id, piece)
        elif etype == "response.function_call_arguments.delta":
            item_id = getattr(event, "item_id", "")
            piece = getattr(event, "delta", "")
            if item_id:
                args_buf[item_id] = args_buf.get(item_id, "") + piece
        elif etype == "response.output_item.done":
            item = getattr(event, "item", None)
            if item is not None and getattr(item, "type", "") == "function_call":
                call_id = getattr(item, "call_id", "")
                name = getattr(item, "name", "")
                arguments = getattr(item, "arguments", "") or args_buf.pop(call_id, "")
                finalized_calls[call_id] = {
                    "name": name,
                    "arguments": arguments,
                }
        elif etype == "response.completed":
            response_obj = getattr(event, "response", None)
            usage = getattr(response_obj, "usage", None) if response_obj else None
            if usage is not None:
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                if record_usage is not None:
                    record_usage(
                        _normalize_usage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            total_tokens=getattr(usage, "total_tokens", None),
                        )
                    )

    # 把 finalized_calls 映射回按 index 排序的 tool_calls（与 OpenAI 后端形状一致）
    tool_calls: dict[int, dict[str, str]] = {}
    for i, (call_id, info) in enumerate(finalized_calls.items()):
        tool_calls[i] = {"id": call_id, "name": info["name"], "arguments": info["arguments"]}
    # 兜底：若没收到 output_item.done 但收到了参数增量
    if not tool_calls and args_buf:
        for i, (item_id, args) in enumerate(args_buf.items()):
            tool_calls[i] = {"id": item_id, "name": "", "arguments": args}

    if cancelled:
        try:
            stream.close()
        except Exception:
            pass
    return text, tool_calls, cancelled


def _stream_anthropic(
    config: type[Config],
    bus: EventBus,
    client: Anthropic,
    tools: ToolRegistry,
    messages: list[ChatCompletionMessageParam],
    mode: str,
    agent_id: str | None,
    record_usage: Callable[[object], None] | None,
    cancel_event: threading.Event | None,
) -> tuple[str, dict[int, dict[str, str]], bool]:
    """Anthropic messages.stream 流式后端。"""
    system_text, chat = _split_system(messages)
    anthropic_messages = _to_anthropic_messages(chat)
    create_kwargs: dict[str, Any] = {
        "model": config.active_model(),
        "messages": anthropic_messages,
        "tools": _to_anthropic_tools(tools.schemas_for_mode(mode)),
        "max_tokens": 8192,
        "stream": True,
    }
    if system_text:
        create_kwargs["system"] = system_text

    stream = client.messages.create(**create_kwargs)

    text = ""
    # 按 index 累积每个 content block；ToolUseBlock 用 index 跟踪
    tool_calls: dict[int, dict[str, str]] = {}
    cancelled = False
    input_tokens = 0
    output_tokens = 0

    for event in stream:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        etype = getattr(event, "type", "")
        if etype == "message_start":
            message = getattr(event, "message", None)
            usage = getattr(message, "usage", None) if message else None
            if usage is not None:
                input_tokens = getattr(usage, "input_tokens", 0) or 0
        elif etype == "content_block_start":
            index = getattr(event, "index", 0)
            block = getattr(event, "content_block", None)
            if block is not None and getattr(block, "type", "") == "tool_use":
                tool_calls[index] = {
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "arguments": "",
                }
        elif etype == "content_block_delta":
            index = getattr(event, "index", 0)
            delta = getattr(event, "delta", None)
            if delta is None:
                continue
            delta_type = getattr(delta, "type", "")
            if delta_type == "text_delta":
                piece = getattr(delta, "text", "")
                if piece:
                    text += piece
                    _publish_token(bus, agent_id, piece)
            elif delta_type == "input_json_delta":
                piece = getattr(delta, "partial_json", "")
                if piece and index in tool_calls:
                    tool_calls[index]["arguments"] += piece
        elif etype == "content_block_stop":
            pass
        elif etype == "message_delta":
            usage = getattr(event, "usage", None)
            if usage is not None:
                output_tokens = getattr(usage, "output_tokens", 0) or 0
        elif etype == "message_stop":
            if record_usage is not None:
                record_usage(
                    _normalize_usage(
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                )

    if cancelled:
        try:
            stream.close()
        except Exception:
            pass
    return text, tool_calls, cancelled


def stream_completion(
    config: type[Config],
    bus: EventBus,
    client: Any,
    tools: ToolRegistry,
    messages: list[ChatCompletionMessageParam],
    mode: str,
    agent_id: str | None = None,
    record_usage: Callable[[object], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    """执行一轮流式请求并处理工具调用，返回 (是否发起工具调用, 累积文本)。

    根据 provider.type 分发到对应后端：
    - openai → chat.completions（OpenAI SDK）
    - responses → Responses API（OpenAI SDK）
    - anthropic → messages.stream（anthropic SDK）

    agent_id 为 None 时发布主对话事件（TOKEN/TOOL_CALL/TOOL_RESULT）；
    否则发布带 agent_id 的子 Agent 事件（SUBAGENT_TOKEN/SUBAGENT_TOOL_CALL/
    SUBAGENT_TOOL_RESULT）。record_usage 非空时把 usage 回调给它（用于主对话计数）。
    cancel_event 置位时中止本轮流式与工具执行。
    """
    if cancel_event is not None and cancel_event.is_set():
        return False, ""
    provider = config.active_provider()
    ptype = provider.type if provider is not None else "openai"
    if ptype == "anthropic":
        text, tool_calls, cancelled = _stream_anthropic(
            config,
            bus,
            client,
            tools,
            messages,
            mode,
            agent_id,
            record_usage,
            cancel_event,
        )
    elif ptype == "responses":
        text, tool_calls, cancelled = _stream_responses(
            config,
            bus,
            client,
            tools,
            messages,
            mode,
            agent_id,
            record_usage,
            cancel_event,
        )
    else:
        text, tool_calls, cancelled = _stream_openai_chat(
            config,
            bus,
            client,
            tools,
            messages,
            mode,
            agent_id,
            record_usage,
            cancel_event,
        )

    if cancelled:
        if text:
            messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=text))
        return False, text

    if not tool_calls:
        if text:
            messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=text))
        return False, text

    assistant_tool_calls = [
        ChatCompletionMessageToolCallParam(
            id=slot["id"],
            type="function",
            function={"name": slot["name"], "arguments": slot["arguments"]},
        )
        for slot in tool_calls.values()
    ]
    messages.append(
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=text or None,
            tool_calls=assistant_tool_calls,
        )
    )

    for slot in tool_calls.values():
        _publish_tool_call(bus, agent_id, slot["name"], slot["arguments"])
        if slot["name"] in config.mode_tool_exclusions(mode):
            result = f"error: 当前模式（{mode}）禁止使用工具 {slot['name']}"
        else:
            result = tools.execute(slot["name"], slot["arguments"], bus)
        _publish_tool_result(bus, agent_id, result)
        messages.append(
            ChatCompletionToolMessageParam(
                role="tool",
                tool_call_id=slot["id"],
                content=result,
            )
        )
    return True, text


logger = get_logger(__name__)


class ChatClient:
    """在后台线程中执行对话请求（按 provider.type 分发到对应后端），通过事件总线推送结果。"""

    def __init__(
        self,
        config: type[Config],
        bus: EventBus,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._tools = tools or ToolRegistry()
        self._client: Any = None
        self._client_params: tuple[str, str, str] | None = None
        provider = config.active_provider()
        if provider is not None and provider.api_key:
            self._client = make_client(config)
            self._client_params = (
                provider.api_key,
                provider.api_url,
                provider.type,
            )
        self._usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "calls": 0,
            "last_input_tokens": 0,
            "last_total_tokens": 0,
        }
        self._cancel = threading.Event()

    def usage_summary(self) -> dict:
        """返回本会话累计的 token 用量快照。"""
        return dict(self._usage)

    def todo_items(self) -> list[dict]:
        """返回当前待办清单快照（供 UI 底部工具栏渲染）。"""
        return self._tools.todo_items()

    def _record_usage(self, usage: object) -> None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or 0
        self._usage["input_tokens"] += prompt_tokens
        self._usage["output_tokens"] += completion_tokens
        self._usage["last_input_tokens"] = prompt_tokens
        self._usage["last_total_tokens"] = total_tokens

    def _ensure_client(self) -> Any:
        """返回与当前 provider 匹配的客户端；切换 provider 后自动重建。"""
        provider = self._config.active_provider()
        if provider is None:
            raise RuntimeError("no active provider")
        api_key = provider.api_key
        base_url = provider.api_url
        ptype = provider.type
        if self._client is None or self._client_params != (api_key, base_url, ptype):
            self._client = make_client(self._config)
            self._client_params = (api_key, base_url, ptype)
        return self._client

    def submit(
        self,
        user_message: str,
        system_prompt: str,
        history: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        """提交一条用户消息，在后台线程中异步执行。"""
        thread = threading.Thread(
            target=self._run,
            args=(user_message, system_prompt, history or []),
            name="chat-worker",
            daemon=True,
        )
        thread.start()

    def cancel(self) -> None:
        """请求中断当前回答；已在流式/工具循环内生效。"""
        self._cancel.set()

    def _run(
        self,
        user_message: str,
        system_prompt: str,
        history: list[ChatCompletionMessageParam],
    ) -> None:
        self._cancel.clear()
        mode = self._config.ACTIVE_MODE
        instructions = self._config.mode_instructions(mode)
        if instructions:
            system_prompt = f"{system_prompt}\n\n{instructions}"
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            *history,
            ChatCompletionUserMessageParam(role="user", content=user_message),
        ]
        try:
            made = True
            while made and not self._cancel.is_set():
                made = self._stream_once(messages, mode)
            self._bus.publish(Event(EventTypes.ASSISTANT_DONE))
        except Exception as exc:
            logger.exception("对话请求失败")
            self._bus.publish(Event(EventTypes.ERROR, str(exc)))

    def _stream_once(self, messages: list[ChatCompletionMessageParam], mode: str) -> bool:
        """执行一轮流式请求；若模型发起了工具调用则执行并返回 True。"""
        self._usage["calls"] += 1
        made, _ = stream_completion(
            self._config,
            self._bus,
            self._ensure_client(),
            self._tools,
            messages,
            mode,
            agent_id=None,
            record_usage=self._record_usage,
            cancel_event=self._cancel,
        )
        return made
