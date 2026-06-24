"""SUID3NUM SUID 权限提权枚举工具"""
from __future__ import annotations
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class Suid3numTool(BaseTool):
    metadata = ToolMetadata(
        name="suid3num",
        description="suid3num 快速枚举 Linux SUID 文件并检索已知提权路径（GTFOBins）。",
        category=ToolCategory.PRIVILEGE_ESCALATION,
        tags=["suid", "privesc", "linux", "gtfobins", "enumeration"],
        references=["https://github.com/Anon-Exploiter/SUID3NUM"],
        parameters=[],
        timeout_default=60.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["python3", "/tmp/suid3num.py"]
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        suid_bins = []
        for line in raw_output.splitlines():
            if "/bin/" in line or "/usr/" in line:
                suid_bins.append(line.strip())
        return {"suid_count": len(suid_bins), "suid_bins": suid_bins[:50]}
