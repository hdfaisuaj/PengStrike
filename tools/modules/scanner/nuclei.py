"""Nuclei 漏洞扫描工具"""
from __future__ import annotations
import json, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class NucleiTool(BaseTool):
    metadata = ToolMetadata(
        name="nuclei",
        description="Nuclei 漏洞扫描器，使用 YAML 模板进行快速、可定制化漏洞检测。",
        category=ToolCategory.VULN_SCAN,
        tags=["nuclei", "scan", "poc", "template", "vulnerability"],
        version="3.2",
        references=["https://github.com/projectdiscovery/nuclei"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标 URL/主机/文件", required=True),
            ToolParameter(name="templates", type="string", description="模板路径或关键字（如 dns/http/cves）", default=""),
            ToolParameter(name="severity", type="string", description="过滤严重性: info/low/medium/high/critical", default=""),
            ToolParameter(name="rate_limit", type="integer", description="每秒请求数", default=150),
            ToolParameter(name="threads", type="integer", description="并发线程数", default=50),
            ToolParameter(name="tags", type="string", description="模板标签（如 sqli,xss）", default=""),
            ToolParameter(name="output_format", type="string", description="输出格式: json/jsonl/list/csv", default="json"),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["nuclei", "-u", kwargs.get("target", "")]

        templates = kwargs.get("templates", "")
        if templates:
            cmd.extend(["-t", templates])

        tags = kwargs.get("tags", "")
        if tags:
            cmd.extend(["-tags", tags])

        severity = kwargs.get("severity", "")
        if severity:
            cmd.extend(["-severity", severity])

        cmd.extend(["-rl", str(kwargs.get("rate_limit", 150))])
        cmd.extend(["-c", str(kwargs.get("threads", 50))])

        fmt = kwargs.get("output_format", "json")
        cmd.extend(["-json", "-o", "/dev/stdout"] if fmt == "json" else ["-o", "/dev/stdout"])

        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        results: List[Dict] = []
        for line in raw_output.splitlines():
            try:
                entry = json.loads(line)
                results.append({
                    "host": entry.get("host", ""),
                    "matched": entry.get("matched-at", ""),
                    "template": entry.get("template-id", ""),
                    "name": entry.get("info", {}).get("name", ""),
                    "severity": entry.get("info", {}).get("severity", ""),
                    "description": entry.get("info", {}).get("description", ""),
                    "cve_id": entry.get("info", {}).get("classification", {}).get("cve-id", [""])[0],
                })
            except Exception:
                pass
        return {"findings_count": len(results), "findings": results} if results else {"findings_count": 0, "findings": []}
