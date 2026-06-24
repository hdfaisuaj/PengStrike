"""Amass DNS 枚举工具"""
from __future__ import annotations
import json, re, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class AmassTool(BaseTool):
    metadata = ToolMetadata(
        name="amass",
        description="Amass DNS 枚举和子域名发现工具，支持被动/主动/枚举模式。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["dns", "amass", "subdomain", "recon", "enum"],
        version="3.23",
        references=["https://github.com/OWASP/Amass"],
        parameters=[
            ToolParameter(name="domain", type="string", description="目标域名", required=True),
            ToolParameter(name="mode", type="string", description="模式: passive/active/enum", default="passive"),
            ToolParameter(name="wordlist", type="string", description="字典路径（enum模式）", default=""),
            ToolParameter(name="output_format", type="string", description="输出格式: json/text", default="json"),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")

        cmd = ["amass"]
        mode = kwargs.get("mode", "passive")
        domain = kwargs.get("domain", "")

        if mode == "passive":
            cmd.append("passive")
        elif mode == "active":
            cmd.append("enum")
        else:
            cmd.append("enum")

        cmd.extend(["-d", domain])

        if kwargs.get("wordlist"):
            cmd.extend(["-w", kwargs["wordlist"]])

        if kwargs.get("output_format") == "json":
            cmd.extend(["-json", "/dev/stdout"])

        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=_timeout if _timeout is not None else self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        subdomains, ips, cnames = set(), set(), set()
        for line in raw_output.splitlines():
            try:
                j = json.loads(line)
                name = j.get("name", "")
                if name:
                    subdomains.add(name)
                for ip in j.get("addresses", []):
                    ips.add(ip.get("ip", ""))
                cnames.update(j.get("cnames", []))
            except Exception:
                m = re.match(r"([\w\-\.]+\.[\w]+)", line)
                if m and "@" not in line:
                    subdomains.add(m.group(1))
        return {"subdomains": sorted(subdomains), "subdomain_count": len(subdomains), "ips": sorted(ips), "cnames": sorted(cnames)}
