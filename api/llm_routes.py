"""
LLM 对话 API 路由 (api/llm_routes.py)

端点:
- POST /api/llm/chat  发送单条消息并获取 AI 回复（非流式，方便前端调用）
- POST /api/llm/chat/stream  SSE 流式对话端点（推荐，前端用 fetch + ReadableStream 读取）

加固内容（Phase 5）:
- 请求体参数合法性校验（空消息、非法角色）
- 标准错误格式，前端可直接解析
- 保留 content + tool_calls 返回结构
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from core.llm_client import LLMClient, SYSTEM_PROMPT

router = APIRouter(prefix="/api/llm", tags=["LLM对话"])


class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        allowed = {"user", "assistant", "system", "tool"}
        if v not in allowed:
            raise ValueError(f"非法角色 '{v}'，允许值: {', '.join(allowed)}")
        return v

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v  # tool 消息或纯 tool_calls 的 assistant 消息允许 content 为 None
        if not v.strip():
            return None  # 空串也转 None
        if len(v) > 100000:
            raise ValueError("消息内容过长（上限 100000 字符）")
        return v.strip()


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    stream: bool = False

    @field_validator("messages")
    @classmethod
    def validate_messages(cls, v: List[ChatMessage]) -> List[ChatMessage]:
        if not v:
            raise ValueError("消息列表不能为空")
        if len(v) > 200:
            raise ValueError("消息列表过长（上限 200 条）")
        if v[0].role not in ("user", "system"):
            raise ValueError("首条消息必须是 user 或 system 角色")
        return v


class ChatResponse(BaseModel):
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """发送对话消息，返回 AI 回复（非流式）。"""
    try:
        client = LLMClient()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM 客户端初始化失败，请检查 LLM 配置: {e}"
        )

    client.messages = [{"role": "system", "content": client.system_prompt}]
    for msg in req.messages:
        m: dict[str, Any] = {"role": msg.role}
        if msg.content is not None:
            m["content"] = msg.content
        if msg.tool_calls:
            m["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        client.messages.append(m)

    # ★ 强制清洗消息，防止 malformed JSON 导致 422
    client.messages = client._sanitize_messages(client.messages)

    try:
        content, tool_call_dicts = client.stream_chat(enable_tools=True)
        return ChatResponse(
            content=content or "",
            tool_calls=tool_call_dicts or None,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "Connection error" in error_msg or "timeout" in error_msg.lower():
            detail = f"LLM API 连接失败，请检查 API 地址和网络: {error_msg}"
        elif "401" in error_msg or "Unauthorized" in error_msg:
            detail = f"LLM API 认证失败，请检查 API Key: {error_msg}"
        elif "model" in error_msg.lower() and "not" in error_msg.lower():
            detail = f"LLM 模型不可用，请检查模型名称: {error_msg}"
        else:
            detail = f"LLM API 调用失败: {error_msg}"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail
        )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """SSE 流式对话端点：逐块返回 AI 回复，前端可实时渲染。

    请求体与 /api/llm/chat 一致。
    响应为 SSE (text/event-stream)，事件类型：
      data: {"type": "content", "text": "..."}\n\n
      data: {"type": "tool_calls", "calls": [...]}\n\n
      data: {"type": "done"}\n\n
      data: {"type": "error", "message": "..."}\n\n

    实现说明：
    - stream_chat_sse() 是同步生成器（OpenAI SDK 的 stream 是同步的）
    - 将同步生成器包装为异步生成器，每个事件 yield 后 await asyncio.sleep(0)
      让出事件循环，确保 SSE 分片及时发送到客户端
    - 后端逐块 yield，前端逐块渲染，实现真正的流式输出
    """
    try:
        client = LLMClient()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM 客户端初始化失败: {e}"
        )

    client.messages = [{"role": "system", "content": client.system_prompt}]
    for msg in req.messages:
        m: dict[str, Any] = {"role": msg.role}
        if msg.content is not None:
            m["content"] = msg.content
        if msg.tool_calls:
            m["tool_calls"] = msg.tool_calls
        if msg.tool_call_id:
            m["tool_call_id"] = msg.tool_call_id
        client.messages.append(m)

    # ★ 强制清洗消息，防止 malformed JSON 导致 422
    client.messages = client._sanitize_messages(client.messages)

    async def event_generator():
        """异步生成器：从同步 SSE 生成器逐块读取，yield 前让出事件循环。"""
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        import queue

        event_queue: queue.Queue = queue.Queue()
        done_signal = object()
        exception_value = None

        def run_sync():
            """在线程中运行同步 SSE 生成器，将每个事件放入队列。"""
            try:
                for event in client.stream_chat_sse(enable_tools=True):
                    event_queue.put(event)
            except Exception as exc:
                import traceback
                logger.error("[SSE] stream_chat_sse 异常: %s\n%s", exc, traceback.format_exc())
                event_queue.put(exc)
            finally:
                event_queue.put(done_signal)

        # 在线程池中运行同步生成器
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_sync)
            while True:
                # 非阻塞地检查队列，设置较短超时以及时响应客户端断开
                try:
                    item = event_queue.get(timeout=0.01)
                except queue.Empty:
                    # 客户端已断开则退出
                    if await request.is_disconnected():
                        future.cancel()
                        return
                    # 未断开则继续等待
                    await asyncio.sleep(0)
                    continue

                if item is done_signal:
                    break

                if isinstance(item, Exception):
                    yield f"data: {json.dumps({'type': 'error', 'message': str(item)}, ensure_ascii=False)}\n\n"
                    break

                # 成功取到事件，序列化为 SSE 分片
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                # 关键：让出事件循环，确保当前分片被及时发送
                await asyncio.sleep(0)

            # 循环结束，发送 done 事件
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )
