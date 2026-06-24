"""Ping 工具"""
from __future__ import annotations
import platform, re, shlex
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class PingTool(BaseTool):
    metadata = ToolMetadata(
        name="ping",
        description="Ping ICMP 主机可达性检测。",
        category=ToolCategory.UTILITY,
        tags=["ping", "icmp", "reachability"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标 IP 或主机名", required=True),
            ToolParameter(name="count", type="integer", description="发送数据包数量", default=4),
            ToolParameter(name="timeout", type="integer", description="超时秒数", default=5),
        ],
        timeout_default=30.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        target = kwargs.get("target", "")
        count = str(kwargs.get("count", 4))
        timeout = str(kwargs.get("timeout", 5))

        is_windows = platform.system().lower().startswith("win")
        if is_windows:
            # Windows: -n = 数据包数, -w = 超时（毫秒）
            cmd = ["ping", "-n", count, "-w", str(int(timeout) * 1000), target]
        else:
            # Linux/macOS: -c = 数据包数, -W = 超时（秒）
            cmd = ["ping", "-c", count, "-W", timeout, target]

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        m = re.search(r"(\d+) packets transmitted, (\d+) received", raw_output)
        if m:
            sent, received = int(m.group(1)), int(m.group(2))
            return {"sent": sent, "received": received, "reachable": received > 0}
        return None
