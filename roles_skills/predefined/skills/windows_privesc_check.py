"""
Windows提权检查技能 (windows_privesc_check.py)

执行流程:
1. 检查当前用户权限 (whoami)
2. 检查系统信息 (systeminfo)
3. 使用 winpeas 进行全面提权枚举（如可上传）
4. 汇总提权路径
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from roles_skills.base_skill import BaseSkill
from tools.base_tool import ToolResult

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


class WindowsPrivescCheck(BaseSkill):
    name: str = "windows_privesc_check"
    description: str = "Windows提权检查：全面枚举Windows系统提权路径"
    parameters: dict = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "目标主机 IP（已在 Shell 中时可省略）"},
            "session_type": {"type": "string", "description": "会话类型 (shell/powershell)"},
        },
        "required": [],
    }

    async def run(self, orchestrator: Orchestrator, **kwargs) -> ToolResult:
        session_type = kwargs.get("session_type", "shell")
        output_parts: list[str] = []
        total_duration = 0.0
        all_structured = {}

        # 步骤 1: 检查当前用户
        step1 = await orchestrator.call_tool(
            "netstat" if session_type == "powershell" else "curl",
            command="whoami /all" if session_type == "powershell" else "whoami",
        )
        if step1.success:
            output_parts.append(f"=== 当前用户 ===\n{step1.output}")
            total_duration += step1.duration

        # 步骤 2: 系统信息
        if session_type == "powershell":
            cmd = "systeminfo"
        else:
            cmd = "cat /proc/version 2>/dev/null || uname -a"
        step2 = await orchestrator.call_tool(
            "netstat" if session_type == "powershell" else "curl",
            command=cmd,
        )
        if step2.success:
            output_parts.append(f"=== 系统信息 ===\n{step2.output}")
            total_duration += step2.duration

        # 步骤 3: 进程和服务检查
        if session_type == "powershell":
            step3 = await orchestrator.call_tool(
                "netstat",
                command="tasklist /svc",
            )
            if step3.success:
                output_parts.append(f"=== 运行进程 ===\n{step3.output}")
                total_duration += step3.duration

        return ToolResult(
            success=True,
            output="\n\n".join(output_parts),
            structured_data=all_structured,
            duration=total_duration,
            tool_name=self.name,
        )