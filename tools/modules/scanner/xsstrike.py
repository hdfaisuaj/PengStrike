"""XSStrike XSS 漏洞检测工具"""
from __future__ import annotations
import shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class XsstrikeTool(BaseTool):
    metadata = ToolMetadata(
        name="xsstrike",
        description="XSStrike XSS 漏洞扫描与检测工具，支持 DOM 扫描和模糊测试。",
        category=ToolCategory.VULN_SCAN,
        tags=["xsstrike", "xss", "web", "vulnerability", "injection"],
        version="4.0",
        references=["https://github.com/s0md3v/XSStrike"],
        parameters=[
            ToolParameter(name="url", type="string", description="目标 URL", required=True),
            ToolParameter(name="method", type="string", description="HTTP 方法", default="GET"),
            ToolParameter(name="data", type="string", description="POST 数据", default=""),
            ToolParameter(name="params", type="boolean", description="扫描 URL 参数", default=True),
            ToolParameter(name="crawl", type="boolean", description="爬取页面发现更多参数", default=False),
            ToolParameter(name="level", type="integer", description="扫描深度级别", default=3),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["python3", "-m", "xsstrike", "-u", kwargs.get("url", "")]
        if kwargs.get("crawl"):
            cmd.append("--crawl")
        if kwargs.get("data"):
            cmd.extend(["--data", kwargs["data"]])
        if not kwargs.get("params"):
            cmd.append("--skip-param")
        cmd.extend(["-l", str(kwargs.get("level", 3))])
        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        findings = [line for line in raw_output.splitlines() if "param" in line.lower() or "xss" in line.lower() or "vulnerable" in line.lower()]
        return {"findings_count": len(findings), "findings": findings[:20]} if findings else None
