"""Nmap NSE 脚本独立执行工具"""
from __future__ import annotations
import json, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class NmapScriptTool(BaseTool):
    metadata = ToolMetadata(
        name="nmap_script",
        description="直接执行 Nmap NSE 脚本（如 http-enum, smb-vuln*, ssl-*, dns-*）。",
        category=ToolCategory.VULN_SCAN,
        tags=["nmap", "nse", "scan", "script", "vulnerability"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标", required=True),
            ToolParameter(name="script", type="string", description="NSE 脚本名（如 http-enum, smb-vuln-ms17-010）", required=True),
            ToolParameter(name="script_args", type="string", description="脚本参数（如 smbuser=admin）", default=""),
            ToolParameter(name="port", type="string", description="指定端口", default=""),
            ToolParameter(name="extra_nmap_args", type="string", description="额外 nmap 参数", default="-sV"),
        ],
        timeout_default=120.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        script = kwargs.get("script", "")
        target = kwargs.get("target", "")
        script_args = kwargs.get("script_args", "")
        port = kwargs.get("port", "")
        extra = kwargs.get("extra_nmap_args", "-sV")
        cmd = ["nmap"]
        cmd.extend(shlex.split(extra))
        if port:
            cmd.extend(["-p", port])
        cmd.extend(["--script", script])
        if script_args:
            cmd.extend(["--script-args", script_args])
        cmd.append(target)

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        # 提取关键发现
        results = [line.strip() for line in raw_output.splitlines() if "|" in line or "State:" in line or "VULNERABLE" in line]
        return {"findings": results[:50], "count": len(results)} if results else None
