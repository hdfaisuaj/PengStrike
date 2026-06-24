"""Netcat 网络瑞士军刀"""
from __future__ import annotations
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class NetcatTool(BaseTool):
    metadata = ToolMetadata(
        name="nc",
        description="Netcat 网络瑞士军刀，支持端口扫描/反弹 Shell/文件传输/代理等。",
        category=ToolCategory.UTILITY,
        tags=["nc", "netcat", "network", "shell", "port"],
        parameters=[
            ToolParameter(name="action", type="string", description="操作: listen/scan/connect", required=True),
            ToolParameter(name="target", type="string", description="目标 IP（connect/scan 模式）", default=""),
            ToolParameter(name="port", type="integer", description="端口", required=True),
            ToolParameter(name="command", type="string", description="交互式 Shell 命令", default=""),
            ToolParameter(name="listen_timeout", type="integer", description="监听超时秒数", default=10),
        ],
        timeout_default=60.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        action = kwargs.get("action", "")
        target = kwargs.get("target", "")
        port = kwargs.get("port", 0)
        cmd_str = kwargs.get("command", "")

        if action == "listen":
            cmd = ["nc", "-nlvp", str(port)]
        elif action == "connect":
            cmd = ["nc", "-nv", target, str(port)]
        else:
            cmd = ["nc", "-znv", target, str(port)]
        if cmd_str:
            cmd.extend(["-e", cmd_str])

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        open_ports = []
        for line in raw_output.splitlines():
            if "open" in line.lower() or "succeeded" in line.lower():
                open_ports.append(line.strip())
        return {"open_ports": open_ports, "count": len(open_ports)}
