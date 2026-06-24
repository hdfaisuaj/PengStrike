"""Ffuf Web 目录/参数模糊测试工具"""
from __future__ import annotations
import json, re
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class FfufTool(BaseTool):
    metadata = ToolMetadata(
        name="ffuf",
        description="Ffuf 快速 Web 模糊测试工具，用于目录/子域名/参数 fuzz。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["ffuf", "fuzz", "web", "dir", "recon"],
        version="2.1",
        references=["https://github.com/ffuf/ffuf"],
        parameters=[
            ToolParameter(name="url", type="string", description="目标 URL（FUZZ 替换位置）", required=True),
            ToolParameter(name="wordlist", type="string", description="字典路径", required=True),
            ToolParameter(name="mode", type="string", description="模式: url/dir/subdomain/url", default="dir"),
            ToolParameter(name="threads", type="integer", description="并发数", default=40),
            ToolParameter(name="match_codes", type="string", description="匹配的状态码（如 200,204,301）", default="200-399"),
            ToolParameter(name="timeout", type="integer", description="超时秒数", default=30),
            ToolParameter(name="output_format", type="string", description="输出格式: json/euclidean", default="json"),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")

        cmd = ["ffuf"]
        url = kwargs.get("url", "")
        wordlist = kwargs.get("wordlist", "")

        cmd.extend(["-u", url, "-w", wordlist])
        cmd.extend(["-t", str(kwargs.get("threads", 40))])
        cmd.extend(["-mc", kwargs.get("match_codes", "200-399")])
        cmd.extend(["-timeout", str(kwargs.get("timeout", 30))])

        if kwargs.get("output_format") == "json":
            cmd.extend(["-o", "/dev/stdout", "-of", "json"])

        return await self._run_cmd(cmd, timeout=_timeout if _timeout is not None else self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(raw_output)
            results = data.get("results", [])
            return {
                "count": len(results),
                "items": [{"url": r.get("url",""), "status": r.get("status",0), "length": r.get("length",0), "words": r.get("words",0)} for r in results],
            }
        except Exception:
            items = []
            for line in raw_output.splitlines():
                m = re.match(r"\s*([\d.]+)\s+(\d+)\s+(\d+)\s+[^\s]+\s+(.+)", line)
                if m:
                    items.append({"ip": m.group(1), "status": int(m.group(2)), "length": int(m.group(3)), "url": m.group(4)})
            return {"count": len(items), "items": items} if items else None
