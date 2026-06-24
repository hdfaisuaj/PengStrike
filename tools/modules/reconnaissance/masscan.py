"""
Masscan 工具 (tools/modules/reconnaissance/masscan.py)

支持:
- 高速批量端口扫描
- 输出格式: --open-only, --json
- 结构化解析 JSON 输出
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class MasscanPort(BaseModel):
    ip: str
    port: int
    proto: str
    timestamp: Optional[str] = None


class MasscanOutput(BaseModel):
    all_hosts: List[str] = Field(default_factory=list)
    all_ports: List[MasscanPort] = Field(default_factory=list)
    port_count: int = 0
    host_count: int = 0


class MasscanTool(BaseTool):
    """Masscan 高速端口扫描器。"""

    metadata = ToolMetadata(
        name="masscan",
        description="Masscan 高速批量端口扫描器，速度可达每秒百万包，支持自定义速率。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["scan", "masscan", "port", "fast"],
        version="1.3",
        references=["https://github.com/robertdavidgraham/masscan"],
        requires_root=True,
        parameters=[
            ToolParameter(name="targets", type="string", description="目标 IP/CIDR 范围", required=True),
            ToolParameter(name="ports", type="string", description="端口范围（如 1-10000,443,80）", required=True),
            ToolParameter(name="rate", type="integer", description="发包速率（每秒包数）", default=10000),
            ToolParameter(name="open_only", type="boolean", description="仅显示开放端口", default=True),
            ToolParameter(name="json", type="boolean", description="JSON 输出格式", default=True),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["masscan"]
        targets = kwargs.get("targets", "")
        ports = kwargs.get("ports", "1-10000")
        rate = kwargs.get("rate", 10000)
        json_output = kwargs.get("json", True)

        cmd.extend(["-p", ports, targets])
        cmd.extend(["--rate", str(rate)])

        if json_output:
            cmd.append("--json")
        if kwargs.get("open_only", True):
            cmd.append("--open-only")

        extra_args = kwargs.get("extra_args", "")
        if extra_args:
            cmd.extend(shlex.split(extra_args))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        try:
            # Masscan JSON 输出格式
            lines = [json.loads(l) for l in raw_output.splitlines() if l.strip().startswith("{")]
            if not lines:
                return None

            ports: List[Dict] = []
            ips: set = set()
            for item in lines:
                if item.get("ports"):
                    for p in item["ports"]:
                        port_entry = {
                            "ip": item.get("ip", ""),
                            "port": p.get("port", 0),
                            "proto": p.get("proto", "tcp"),
                            "timestamp": p.get("timestamp"),
                        }
                        ports.append(port_entry)
                        ips.add(item.get("ip", ""))
            return {
                "all_hosts": sorted(ips),
                "all_ports": ports,
                "port_count": len(ports),
                "host_count": len(ips),
            }
        except Exception:
            # 正则降级
            return self._parse_regex(raw_output)

    def _parse_regex(self, text: str) -> Dict[str, Any]:
        ports: List[Dict] = []
        ips: set = set()
        for line in text.splitlines():
            m = re.match(r"Discovered open port (\d+)/(tcp|udp) on ([\d.]+)", line)
            if m:
                ports.append({"port": int(m.group(1)), "proto": m.group(2), "ip": m.group(3)})
                ips.add(m.group(3))
        return {"all_hosts": sorted(ips), "all_ports": ports, "port_count": len(ports), "host_count": len(ips)}
