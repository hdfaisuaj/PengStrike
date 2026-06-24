"""Whois 查询工具"""
from __future__ import annotations
import re
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class WhoisTool(BaseTool):
    metadata = ToolMetadata(
        name="whois",
        description="Whois 域名/IP 信息查询。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["whois", "dns", "domain", "registration"],
        parameters=[
            ToolParameter(name="target", type="string", description="域名或 IP 地址", required=True),
        ],
        timeout_default=30.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")
        cmd = ["whois", kwargs.get("target", "")]
        return await self._run_cmd(cmd, timeout=_timeout if _timeout is not None else self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        data: Dict[str, Any] = {}
        fields = {"Domain Name": "domain", "Registrar": "registrar", "Registrant Country": "country",
                  "Creation Date": "created", "Expiry Date": "expires", "Name Server": "nameservers",
                  "Registrar URL": "registrar_url", "DNSSEC": "dnssec"}
        for key, val_name in fields.items():
            m = re.search(f"{key}[^:]*:\\s*(.+)", raw_output, re.IGNORECASE)
            if m:
                data[val_name] = m.group(1).strip()
        return data if data else None
