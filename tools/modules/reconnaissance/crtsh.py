"""Crtsh (Certificate Transparency) 子域名发现"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class CrtshTool(BaseTool):
    metadata = ToolMetadata(
        name="crtsh",
        description="通过 Certificate Transparency 日志发现子域名（被动收集，无需发送流量到目标）。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["crt.sh", "certificate", "subdomain", "passive", "dns"],
        parameters=[
            ToolParameter(name="domain", type="string", description="目标域名", required=True),
            ToolParameter(name="limit", type="integer", description="最大返回条数", default=1000),
        ],
        timeout_default=60.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")
        timeout = _timeout if _timeout is not None else 60

        domain = kwargs.get("domain", "")
        limit = kwargs.get("limit", 1000)
        url = f"https://crt.sh/?q=%.{domain}&output=json&limit={limit}"

        cmd = ["curl", "-s", "--max-time", str(int(timeout)), "-H", f"User-Agent: PengStrike/3.0", url]

        return await self._run_cmd(cmd, timeout=timeout)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        import json
        subdomains = set()
        try:
            for entry in json.loads(raw_output):
                name = entry.get("name_value", "")
                for subdomain in name.split("\n"):
                    subdomain = subdomain.strip()
                    if subdomain and subdomain != domain:
                        subdomains.add(subdomain)
        except Exception:
            for m in re.findall(r'([\w\-\.]+\.' + re.escape("example.com") + r')', raw_output):
                subdomains.add(m)
        return {"subdomains": sorted(subdomains), "count": len(subdomains)}
