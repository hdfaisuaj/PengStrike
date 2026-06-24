"""
Gobuster 工具 (tools/modules/reconnaissance/gobuster.py)

支持:
- 目录爆破 (dir)
- DNS 子域名爆破 (dns)
- 虚拟主机发现 (vhost)
- 输出格式: json, csv
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Dict, List, Optional

from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class GobusterTool(BaseTool):
    """Gobuster 目录/子域名爆破工具。"""

    metadata = ToolMetadata(
        name="gobuster",
        description="Gobuster 目录、子域名、虚拟主机爆破工具，支持多种模式。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["gobuster", "dir", "dns", "vhost", "brute", "web"],
        version="3.6",
        references=["https://github.com/OJ/gobuster"],
        parameters=[
            ToolParameter(name="mode", type="string", description="模式: dir/dns/vhost/fuzz", default="dir"),
            ToolParameter(name="url", type="string", description="目标 URL（dir/vhost/fuzz 模式）"),
            ToolParameter(name="domain", type="string", description="目标域名（dns 模式）"),
            ToolParameter(name="wordlist", type="string", description="字典路径", required=True),
            ToolParameter(name="extensions", type="string", description="文件扩展名（如 php,html,txt）", default=""),
            ToolParameter(name="threads", type="integer", description="并发线程数", default=10),
            ToolParameter(name="timeout", type="integer", description="超时秒数", default=10),
            ToolParameter(name="output_format", type="string", description="输出格式: json/status/raw", default="json"),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        _timeout = kwargs.get("_timeout")

        cmd = ["gobuster"]
        mode = kwargs.get("mode", "dir")
        wordlist = kwargs.get("wordlist", "")
        output_format = kwargs.get("output_format", "json")

        cmd.append(mode)

        if mode == "dir":
            url = kwargs.get("url", "")
            if not url:
                return ToolResult(success=False, output="", error="dir 模式需要 url 参数", tool_name="gobuster")
            cmd.extend(["-u", url])
        elif mode == "dns":
            domain = kwargs.get("domain", "")
            if not domain:
                return ToolResult(success=False, output="", error="dns 模式需要 domain 参数", tool_name="gobuster")
            cmd.extend(["-d", domain])
        else:
            url = kwargs.get("url", "")
            if url:
                cmd.extend(["-u", url])

        cmd.extend(["-w", wordlist])
        cmd.extend(["-t", str(kwargs.get("threads", 10))])
        cmd.extend(["--timeout", f"{kwargs.get('timeout', 10)}s"])

        extensions = kwargs.get("extensions", "")
        if extensions:
            cmd.extend(["-x", extensions])

        if output_format == "json":
            cmd.extend(["-o", "/dev/stdout"])
            cmd.append("--no-error")

        extra_args = kwargs.get("extra_args", "")
        if extra_args:
            cmd.extend(shlex.split(extra_args))

        return await self._run_cmd(cmd, timeout=_timeout if _timeout is not None else self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        # 尝试 JSON 解析
        try:
            # Gobuster JSON 输出通常以 [] 包裹
            for line in raw_output.splitlines():
                if line.strip().startswith("["):
                    continue
                if line.strip().startswith("{"):
                    data = json.loads(line)
                    results = data.get("Results", [])
                    return {
                        "found": len(results),
                        "items": [
                            {
                                "url": r.get("URL", ""),
                                "status": r.get("Status", 0),
                                "length": r.get("ContentLength", 0),
                                "name": r.get("Name", ""),
                            }
                            for r in results
                        ],
                    }
        except Exception:
            pass

        # 正则解析
        items: List[Dict] = []
        for line in raw_output.splitlines():
            m = re.match(r"(\[+\s*(\d+)\]?\s+)?(http[s]?://[^\s]+)\s+([\d]+)\s+([\w\-]+)", line)
            if m:
                items.append({
                    "url": m.group(3),
                    "status": int(m.group(4)),
                    "length": m.group(5),
                })
        return {"found": len(items), "items": items} if items else None
