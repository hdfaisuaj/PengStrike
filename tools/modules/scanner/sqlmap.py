"""Sqlmap SQL 注入检测与利用工具"""
from __future__ import annotations
import json, re, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class SqlmapTool(BaseTool):
    metadata = ToolMetadata(
        name="sqlmap",
        description="Sqlmap 自动 SQL 注入检测与利用工具，支持多种注入技术、数据库枚举、Shell 获取。",
        category=ToolCategory.VULN_SCAN,
        tags=["sqlmap", "sqli", "injection", "database", "web"],
        version="1.8",
        references=["https://sqlmap.org/"],
        parameters=[
            ToolParameter(name="url", type="string", description="目标 URL", required=True),
            ToolParameter(name="method", type="string", description="HTTP 方法: GET/POST", default="GET"),
            ToolParameter(name="data", type="string", description="POST 数据", default=""),
            ToolParameter(name="cookie", type="string", description="Cookie 值", default=""),
            ToolParameter(name="level", type="integer", description="检测级别 1-5", default=1),
            ToolParameter(name="risk", type="integer", description="测试风险级别 1-3", default=1),
            ToolParameter(name="technique", type="string", description="注入技术: B/E/U/S/T/Q", default="BEUSTQ"),
            ToolParameter(name="dbs", type="boolean", description="枚举数据库", default=False),
            ToolParameter(name="tables", type="boolean", description="枚举表", default=False),
            ToolParameter(name="dump", type="boolean", description="Dump 数据", default=False),
            ToolParameter(name="os_shell", type="boolean", description="获取 OS Shell", default=False),
            ToolParameter(name="batch", type="boolean", description="非交互批处理模式", default=True),
            ToolParameter(name="output_format", type="string", description="输出格式: text/json", default="json"),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["sqlmap", "-u", kwargs.get("url", "")]

        if kwargs.get("method", "GET").upper() == "POST":
            cmd.append("--method=POST")
        if kwargs.get("data"):
            cmd.extend(["--data", kwargs["data"]])
        if kwargs.get("cookie"):
            cmd.extend(["--cookie", kwargs["cookie"]])

        cmd.extend(["--level", str(kwargs.get("level", 1))])
        cmd.extend(["--risk", str(kwargs.get("risk", 1))])

        tech = kwargs.get("technique", "BEUSTQ")
        if tech:
            cmd.extend(["--technique", tech])

        if kwargs.get("dbs"):
            cmd.append("--dbs")
        if kwargs.get("tables"):
            cmd.append("--tables")
        if kwargs.get("dump"):
            cmd.append("--dump")
        if kwargs.get("os_shell"):
            cmd.append("--os-shell")

        if kwargs.get("batch"):
            cmd.append("--batch")

        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        vulnerable = any(kw in raw_output.lower() for kw in ["vulnerable", "is vulnerable", "injection found"])
        databases, tables = [], []
        for line in raw_output.splitlines():
            if "available databases" in line.lower() or re.search(r"Database:\s+\w+", line):
                m = re.search(r"Database:\s+(.+)", line)
                if m: databases.append(m.group(1).strip())
            if re.search(r"Table:\s+[\w\.]+", line):
                m = re.search(r"Table:\s+([\w\.]+)", line)
                if m: tables.append(m.group(1).strip())
        return {
            "vulnerable": vulnerable,
            "databases": list(dict.fromkeys(databases)),
            "tables": list(dict.fromkeys(tables))[:100],
        }
