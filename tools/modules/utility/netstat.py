"""Netstat 网络连接状态工具"""
from __future__ import annotations
import re, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class NetstatTool(BaseTool):
    metadata = ToolMetadata(
        name="netstat",
        description="Netstat 网络连接状态查看工具（支持 -tulpn）。",
        category=ToolCategory.UTILITY,
        tags=["netstat", "network", "connections", "port"],
        parameters=[
            ToolParameter(name="options", type="string", description="netstat 选项: tulpn/all/ano", default="tulpn"),
            ToolParameter(name="target", type="string", description="过滤目标 IP", default=""),
        ],
        timeout_default=15.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["netstat"] + kwargs.get("options", "tulpn").split()
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        connections: List[Dict] = []
        for line in raw_output.splitlines():
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 4 and "/" in line:
                proto = parts[0]
                local = parts[3] if len(parts) > 3 else ""
                state = parts[len(parts)-2] if len(parts) > 1 else ""
                program = parts[-1]
                connections.append({"proto": proto, "local": local, "state": state, "program": program})
        listening = [c for c in connections if "LISTEN" in c.get("state", "")]
        return {"total": len(connections), "listening": listening}
