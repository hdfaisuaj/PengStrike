"""
LLM 工具调用执行 API 路由 (api/tool_routes_exec.py)

端点:
- POST /api/tools/execute  执行 LLM 返回的工具调用（如 execute_kali_command）

调用链路：
 前端收到 LLM tool_calls
   → POST /api/tools/execute { name, arguments }
   → 安全校验 (SecurityGuard)
   → AsyncExecutor 执行命令
   → 返回结构化的执行结果
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, validator

from core.security import SecurityGuard
from tools.executor import get_executor
from utils.logger import get_logger

# WS 广播工具函数
from api.websocket import get_connection_manager


async def _ws_broadcast(payload: dict) -> None:
    """向所有前端广播 WebSocket 消息（非致命）。"""
    try:
        manager = get_connection_manager()
        await manager.broadcast(payload)
    except Exception:
        pass

logger = get_logger(__name__)
router = APIRouter(prefix="/api/tools", tags=["工具调用执行"])


class ExecuteToolCallRequest(BaseModel):
    """LLM 工具调用请求。"""
    name: str
    arguments: Any = {}

    @validator("arguments")
    def parse_arguments(cls, v):
        """容错：arguments 可能是 JSON 字符串，自动解析。"""
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                # 不是 JSON，当成 {command: v}
                return {"command": v}
        if v is None:
            return {}
        return v


class NextStep(BaseModel):
    """失败后的下一步建议。"""
    label: str
    action: str
    command: Optional[str] = None

class ExecuteToolCallResponse(BaseModel):
    success: bool
    output: str = ""
    error: Optional[str] = None
    duration: float = 0.0
    return_code: Optional[int] = None
    blocked: bool = False
    blocked_reason: Optional[str] = None
    requires_user_decision: bool = False
    retry_reason: Optional[str] = None
    next_steps: List[NextStep] = []
    manual: bool = False  # 是否为手动执行类型命令


# ========================================================================
# 命令失败分析
# ========================================================================
SCANNING_TOOLS = [
    "gobuster", "dirsearch", "dirb", "ffuf", "nikto",
    "nmap", "masscan", "wpscan", "sqlmap", "hydra",
    "whatweb", "theharvester", "dnsrecon", "sublist3r",
]

# 手动执行类型命令：不自动执行，提示用户在终端手动执行
MANUAL_COMMANDS = {
    "find", "locate", "mlocate", "grep", "egrep", "fgrep", "rg", "ag",
    "ls", "ll", "cat", "head", "tail", "less", "more", "which", "whereis",
    "file", "stat", "wc", "sort", "uniq", "cut", "echo",
    "pwd", "id", "whoami", "hostname", "uname", "date",
}

def _analyze_command_failure(command: str, return_code: int,
                              output: str = "", error: str = "") -> dict:
    """分析命令失败原因，生成下一步建议。"""
    cmd_name = command.split()[0].lower() if command else ""
    full_text = (output + (error or "")).lower()
    reasons: List[str] = []
    steps: List[dict] = []

    if not command:
        return {"requires_user_decision": False, "next_steps": []}

    is_scanning = cmd_name in SCANNING_TOOLS

    # --- 按错误关键词分类 ---
    if any(k in full_text for k in ["not found", "no such file", "cannot find",
                                      "command not found", "permission denied"]):
        reasons.append("命令或文件不存在")
        steps.append({"label": "检查环境", "action": "check_environment",
                       "command": f"which {cmd_name} && {cmd_name} --help 2>&1 | head -5"})
        steps.append({"label": "查看 wordlist", "action": "check_environment",
                       "command": "ls /usr/share/wordlists/ 2>/dev/null | head -20"})

    elif any(k in full_text for k in ["timeout", "timed out", "connection refused",
                                        "connection failed", "no route", "unreachable"]):
        reasons.append("目标连接超时或被拒绝")
        steps.append({"label": "降低并发重试", "action": "retry_lower_threads",
                       "command": _build_lower_concurrency_command(command)})
        steps.append({"label": "检查目标状态", "action": "check_environment",
                       "command": "timeout 5 curl -sI http://192.168.123.138 2>&1 | head -10"})

    elif any(k in full_text for k in ["invalid", "option", "usage:", "unrecognized"]):
        reasons.append("命令参数格式错误")
        steps.append({"label": "查看帮助", "action": "check_environment",
                       "command": f"{cmd_name} --help 2>&1 | head -20"})
        steps.append({"label": "修正后重试", "action": "retry_lower_threads",
                       "command": command})

    elif "wordlist" in full_text or "wordlist" in command.lower():
        reasons.append("wordlist 文件不存在或路径错误")
        steps.append({"label": "使用默认 wordlist", "action": "retry_lower_threads",
                       "command": _build_default_wordlist_command(command)})
        steps.append({"label": "查看可用 wordlist", "action": "check_environment",
                       "command": "ls /usr/share/wordlists/ 2>/dev/null | head -20"})

    elif return_code == 1 and is_scanning:
        reasons.append("扫描工具执行失败，可能原因：目标响应慢、并发过高、参数错误")
        steps.append({"label": "降低并发重试", "action": "retry_lower_threads",
                       "command": _build_lower_concurrency_command(command)})
        steps.append({"label": "改用其他工具", "action": "use_alternative_tool",
                       "command": _build_alternative_command(command)})

    elif return_code == 1:
        reasons.append("命令执行失败，返回码 1")
        steps.append({"label": "重试", "action": "retry_lower_threads",
                       "command": command})

    elif return_code in (2, 255):
        reasons.append(f"命令执行异常，返回码 {return_code}")
        steps.append({"label": "重试", "action": "retry_lower_threads",
                       "command": command})

    else:
        reasons.append(f"命令执行失败，返回码 {return_code}")
        steps.append({"label": "重试", "action": "retry_lower_threads",
                       "command": command})

    # 通用：跳过
    steps.append({"label": "跳过", "action": "skip"})

    # 去重（最多保留 3 个实质性步骤 + skip）
    seen_actions = set()
    unique_steps = []
    for s in steps:
        if s["action"] not in seen_actions:
            seen_actions.add(s["action"])
            unique_steps.append(s)
        if len(unique_steps) >= 3 and s["action"] == "skip":
            unique_steps.append(s)
            break

    return {
        "requires_user_decision": True,
        "retry_reason": "；".join(reasons) if reasons else f"命令执行失败（返回码 {return_code}）",
        "next_steps": unique_steps[:4],
    }


def _build_lower_concurrency_command(command: str) -> str:
    """降低原始命令的并发/线程数。"""
    import re
    # 替换 -t 参数（gobuster/dirsearch 通用）
    lowered = re.sub(r'-t\s+\d+', '-t 10', command)
    # 替换 --threads 参数
    lowered = re.sub(r'--threads\s+\d+', '--threads 10', lowered)
    # 如果没找到线程参数，追加 -t 10
    if lowered == command:
        lowered = command + " -t 10"
    return lowered


def _build_default_wordlist_command(command: str) -> str:
    """替换 wordlist 为更通用的默认路径。"""
    import re
    # 替换 wordlist 路径
    default_wordlist = "/usr/share/wordlists/dirb/common.txt"
    lowered = re.sub(r'-w\s+\S+', f'-w {default_wordlist}', command)
    # 同时降低并发
    lowered = _build_lower_concurrency_command(lowered)
    return lowered


def _build_alternative_command(command: str) -> str:
    """根据原命令生成替代工具的推荐命令。"""
    cmd_name = command.split()[0].lower() if command else ""

    alternatives = {
        "gobuster": f"dirsearch -u http://192.168.123.138 -e php,html,txt,js,css -t 10 -x 404,403",
        "dirsearch": f"gobuster dir -u http://192.168.123.138 -w /usr/share/wordlists/dirb/common.txt -t 10",
        "nmap": f"masscan 192.168.123.138 --ports 1-1000 --rate=500",
        "nikto": f"wpscan --url http://192.168.123.138 2>/dev/null || whatweb -a 3 http://192.168.123.138",
        "hydra": f"medusa -h 192.168.123.138 -U users.txt -P passwords.txt -M ssh",
    }
    return alternatives.get(cmd_name, f"# 请手动指定替代命令")


@router.post("/execute", response_model=ExecuteToolCallResponse)
async def execute_tool_call(
    req: ExecuteToolCallRequest,
    request: Request,
):
    """执行 LLM 工具调用，兼容 execute_kali_command 及其他内置工具。

    流程：
    1. 匹配工具名称
    2. 提取待执行命令
    3. 安全检查
    4. 执行命令
    5. 返回结构化结果
    """
    # 调试日志：记录收到的请求
    body = await request.body()
    logger.info("[工具执行] 收到请求: %s", body.decode("utf-8", errors="replace"))

    tool_name = req.name
    arguments = req.arguments
    if not isinstance(arguments, dict):
        # validator 应该已经处理了，再兜底一次
        arguments = {}
        logger.warning("[工具执行] arguments 不是 dict，已重置为空: %r", req.arguments)
    start_time = time.monotonic()

    if not tool_name:
        raise HTTPException(status_code=400, detail="工具名称不能为空")

    # --- 1. 提取命令 ---
    command = ""
    if tool_name == "execute_kali_command":
        command = arguments.get("command", "")
        if not command or not command.strip():
            return ExecuteToolCallResponse(
                success=False,
                output="",
                error="缺少 command 参数",
                duration=time.monotonic() - start_time,
            )
    elif tool_name == "save_exploit":
        source_path = arguments.get("source_path", "")
        if not source_path or not source_path.strip():
            return ExecuteToolCallResponse(
                success=False,
                error="缺少 source_path 参数",
                duration=time.monotonic() - start_time,
            )
        # 目标路径：项目根目录下的 exploit/
        exploit_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exploit")
        os.makedirs(exploit_dir, exist_ok=True)
        filename = os.path.basename(source_path)
        target_path = os.path.join(exploit_dir, filename)
        try:
            shutil.copy2(source_path, target_path)
            return ExecuteToolCallResponse(
                success=True,
                output=f"✅ 已复制到 {target_path}",
                duration=time.monotonic() - start_time,
            )
        except Exception as e:
            return ExecuteToolCallResponse(
                success=False,
                error=f"复制失败: {e}",
                duration=time.monotonic() - start_time,
            )
    else:
        # 兼容传统工具调用（name 作为工具名，arguments 作为执行参数）
        command = arguments.get("command") or arguments.get("cmd", "")
        if not command:
            # 尝试从 arguments 构造命令
            target = arguments.get("target") or arguments.get("host", "")
            if target:
                command = f"{tool_name} {target}"
            else:
                command = tool_name

    # --- 2. 广播：工具即将执行 ---
    if command and command.strip():
        asyncio.ensure_future(_ws_broadcast({
            "type": "tool_exec_started",
            "tool": tool_name,
            "command": command[:200],
            "timestamp": time.time(),
        }))

    # --- 3. 安全检查 ---
    guard = SecurityGuard()
    # 如果用户已确认（强制执行），跳过安全检查
    user_confirmed = arguments.get("user_confirmed", False) if isinstance(arguments, dict) else False
    if not user_confirmed:
        safe, reason = guard.check_command(command)
    else:
        safe, reason = True, ""
    if not safe:
        logger.warning("工具调用被安全拦截: %s => %s", command, reason)
        # 广播：命令被拦截
        asyncio.ensure_future(_ws_broadcast({
            "type": "tool_exec_blocked",
            "tool": tool_name,
            "command": command[:300],
            "reason": reason,
            "timestamp": time.time(),
        }))
        return ExecuteToolCallResponse(
            success=False,
            output="",
            error=f"命令被安全拦截: {reason}",
            blocked=True,
            blocked_reason=reason,
            duration=time.monotonic() - start_time,
        )

    # --- 3.5 手动执行命令检查 ---
    cmd_name = command.split()[0].lower() if command else ""
    if cmd_name in MANUAL_COMMANDS:
        logger.info("命令匹配手动执行类型，跳过自动执行: %s", command)
        return ExecuteToolCallResponse(
            success=False,
            output="",
            error="此命令建议在终端手动执行",
            manual=True,
            requires_user_decision=True,
            retry_reason="此命令建议用户在终端手动执行",
            next_steps=[
                NextStep(label="去终端执行", action="manual_execution", command=command),
                NextStep(label="跳过", action="skip"),
                NextStep(label="修改命令", action="modify", command=command),
            ],
            duration=time.monotonic() - start_time,
        )

    # --- 4. 执行命令 ---
    executor = get_executor()
    try:
        # 分割命令字符串为参数列表（安全模式，shell=False）
        import shlex
        args_list = shlex.split(command)

        result = await executor.execute(
            tool_name=tool_name,
            args=args_list,
            shell_command=command,
        )

        # 失败时自动分析原因并生成下一步建议
        next_steps_data = {}
        if not result.success and result.return_code is not None:
            next_steps_data = _analyze_command_failure(
                command=command,
                return_code=result.return_code,
                output=result.output or "",
                error=result.error or "",
            )

        return ExecuteToolCallResponse(
            success=result.success,
            output=result.output[:10000],  # 截断过长输出
            error=result.error,
            duration=result.duration,
            return_code=result.return_code,
            **next_steps_data,
        )

        # 广播：工具执行结果
        asyncio.ensure_future(_ws_broadcast({
            "type": "tool_exec_result",
            "tool": tool_name,
            "command": command[:200],
            "success": result.success,
            "output": (result.output or "")[:500],
            "error": result.error,
            "return_code": result.return_code,
            "duration": result.duration,
            "timestamp": time.time(),
        }))

    except Exception as exc:
        logger.error("工具执行异常: %s: %s", tool_name, exc)
        return ExecuteToolCallResponse(
            success=False,
            output="",
            error=f"{type(exc).__name__}: {exc}",
            duration=time.monotonic() - start_time,
        )


# ========================================================================
# 强制停止正在运行的工具进程
# ========================================================================
class StopToolResponse(BaseModel):
    success: bool
    killed_count: int = 0
    active_processes: List[str] = []


@router.post("/stop", response_model=StopToolResponse)
async def stop_tool():
    """强制终止所有正在后端运行的工具进程。"""
    executor = get_executor()
    active = executor.get_active_processes()
    killed_count = executor.kill_all()
    logger.warning("[工具停止] 已强制终止 %d 个进程: %s", killed_count, active)
    return StopToolResponse(
        success=True,
        killed_count=killed_count,
        active_processes=active,
    )
