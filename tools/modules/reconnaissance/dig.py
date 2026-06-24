"""Dig DNS 查询工具"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class DigTool(BaseTool):
    metadata = ToolMetadata(
        name="dig",
        description="Dig DNS 查询工具，支持 A/AAAA/MX/NS/TXT/SPF/DMARC 等记录类型。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["dig", "dns", "query", "recon"],
        parameters=[
            ToolParameter(name="target", type="string", description="查询目标（域名）", required=True),
            ToolParameter(name="record_type", type="string", description="记录类型: A/AAAA/MX/NS/TXT/SOA/ANY", default="A"),
            ToolParameter(name="server", type="string", description="指定 DNS 服务器", default=""),
            ToolParameter(name="short", type="boolean", description="简短输出", default=False),
        ],
        timeout_default=30.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")

        cmd = ["dig"]
        target = kwargs.get("target", "")
        record_type = kwargs.get("record_type", "A").upper()
        cmd.append("+short" if kwargs.get("short") else "+noall")
        if kwargs.get("server"):
            cmd.append(f"@{kwargs['server']}")
        cmd.append(target)
        cmd.append(record_type)
        if not kwargs.get("short"):
            cmd.append("+answer")

        return await self._run_cmd(cmd, timeout=_timeout if _timeout is not None else self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        records = []
        for line in raw_output.splitlines():
            m = re.match(r"([\w\-\.]+\.[\w]+)\.\s+\d+\s+IN\s+(\w+)\s+(.+)", line)
            if m:
                records.append({"domain": m.group(1), "type": m.group(2), "value": m.group(3)})
        return {"records": records, "count": len(records)} if records else None
