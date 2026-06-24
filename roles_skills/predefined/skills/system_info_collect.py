"""
系统信息收集技能 (system_info_collect.py)

执行流程:
1. 操作系统版本 (uname -a 或 systeminfo)
2. 网络配置 (ifconfig/ipconfig)
3. 路由表 (route/netstat -r)
4. DNS 解析 (/etc/resolv.conf)
5. 已安装软件/补丁列表
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from roles_skills.base_skill import BaseSkill
from tools.base_tool import ToolResult

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


class SystemInfoCollect(BaseSkill):
    name: str = "system_info_collect"
    description: str = "系统信息收集：目标主机的系统版本、网络配置、路由信息收集"
    parameters: dict = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "目标主机 IP"},
            "os_type": {"type": "string", "enum": ["linux", "windows", "auto"], "description": "操作系统类型"},
        },
        "required": [],
    }

    async def run(self, orchestrator: Orchestrator, **kwargs) -> ToolResult:
        target = kwargs.get("target", "")
        os_type = kwargs.get("os_type", "auto")
        output_parts: list[str] = []
        total_duration = 0.0

        linux_cmds = [
            ("uname", ["uname", "-a"]),
            ("network", ["ip", "addr"]),
            ("route", ["ip", "route"]),
            ("dns", ["cat", "/etc/resolv.conf"]),
            ("hostname", ["hostnamectl"]),
        ]

        windows_cmds = [
            ("systeminfo", ["systeminfo"]),
            ("ipconfig", ["ipconfig", "/all"]),
            ("route", ["route", "print"]),
            ("hotfix", ["wmic", "qfe", "list"]),
        ]

        cmds = linux_cmds if os_type == "linux" else windows_cmds if os_type == "windows" else linux_cmds

        for name, cmd in cmds:
            try:
                result = await orchestrator.call_tool(
                    "netstat",
                    command=" ".join(cmd),
                )
                if result.success:
                    output_parts.append(f"=== {name} ===\n{result.output[:1000]}")
                    total_duration += result.duration
            except Exception:
                continue

        return ToolResult(
            success=True,
            output="\n\n".join(output_parts),
            structured_data={"os_type": os_type, "collected_modules": [c[0] for c in cmds]},
            duration=total_duration,
            tool_name=self.name,
        )