"""Nikto Web 服务器扫描工具"""
from __future__ import annotations
import json, re, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class NiktoTool(BaseTool):
    metadata = ToolMetadata(
        name="nikto",
        description="Nikto Web 服务器安全扫描器，检测潜在漏洞、配置问题、危险文件。",
        category=ToolCategory.VULN_SCAN,
        tags=["nikto", "web", "scan", "vulnerability", "http"],
        version="2.5",
        references=["https://github.com/sullo/nikto"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标 URL 或 IP:PORT", required=True),
            ToolParameter(name="ssl", type="boolean", description="强制 SSL/HTTPS", default=False),
            ToolParameter(name="tuning", type="string", description="扫描类型: 1-9/x/i/e (参考 nikto 文档)", default=""),
            ToolParameter(name="output_format", type="string", description="输出格式: xml/json/csv/text", default="json"),
            ToolParameter(name="dbcheck", type="boolean", description="仅检查 nikto 数据库", default=False),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["nikto", "-h", kwargs.get("target", "")]

        if kwargs.get("ssl"):
            cmd.append("-ssl")

        tuning = kwargs.get("tuning", "")
        if tuning:
            cmd.extend(["-Tuning", tuning])

        fmt = kwargs.get("output_format", "json")
        if fmt == "json":
            cmd.extend(["-Format", "json", "-output", "/dev/stdout"])
        elif fmt == "xml":
            cmd.extend(["-Format", "xml", "-output", "/dev/stdout"])
        else:
            cmd.extend(["-Format", "txt", "-output", "/dev/stdout"])

        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        findings: List[Dict] = []
        for line in raw_output.splitlines():
            # +item: 发现项
            m = re.match(r"\+ [^\n]+", line)
            if m:
                findings.append({"text": line.strip()})
        return {"findings_count": len(findings), "findings": findings[:50]} if findings else None
