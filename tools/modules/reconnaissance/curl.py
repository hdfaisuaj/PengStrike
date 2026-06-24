"""Curl HTTP 请求工具"""
from __future__ import annotations
import json, re, shlex
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class CurlTool(BaseTool):
    metadata = ToolMetadata(
        name="curl",
        description="Curl HTTP 客户端工具，支持自定义 Header/Method/Body/认证，用于 Web 测试。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["curl", "http", "web", "request"],
        parameters=[
            ToolParameter(name="url", type="string", description="目标 URL", required=True),
            ToolParameter(name="method", type="string", description="HTTP 方法", default="GET"),
            ToolParameter(name="headers", type="string", description="自定义 Header（JSON 格式）", default=""),
            ToolParameter(name="data", type="string", description="POST 数据", default=""),
            ToolParameter(name="user_agent", type="string", description="User-Agent", default="PengStrike/3.0"),
            ToolParameter(name="follow_redirects", type="boolean", description="跟随重定向", default=True),
            ToolParameter(name="insecure", type="boolean", description="忽略 SSL 证书错误", default=True),
            ToolParameter(name="timeout", type="integer", description="超时秒数", default=30),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=60.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")

        cmd = ["curl", "-s", "-w", "\\n%{http_code}\\n%{time_total}"]
        url = kwargs.get("url", "")
        method = kwargs.get("method", "GET").upper()
        headers_raw = kwargs.get("headers", "")
        data = kwargs.get("data", "")
        follow = kwargs.get("follow_redirects", True)
        insecure = kwargs.get("insecure", True)
        curl_timeout = kwargs.get("timeout", 30)

        cmd.append("-X")
        cmd.append(method)
        cmd.extend(["-L"] if follow else [])
        cmd.extend(["-k"] if insecure else [])
        cmd.extend(["--max-time", str(curl_timeout)])

        if headers_raw:
            try:
                headers = json.loads(headers_raw)
                for k, v in headers.items():
                    cmd.extend(["-H", f"{k}: {v}"])
            except Exception:
                cmd.extend(["-H", headers_raw])

        ua = kwargs.get("user_agent", "PengStrike/3.0")
        cmd.extend(["-A", ua])

        if data:
            cmd.extend(["-d", data])

        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        cmd.append(url)

        return await self._run_cmd(cmd, timeout=_timeout if _timeout is not None else self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        return {"raw_length": len(raw_output), "preview": raw_output[:500]}
