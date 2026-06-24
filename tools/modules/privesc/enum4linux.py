"""enum4linux Windows/SMB 信息枚举"""
from __future__ import annotations
import re, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class Enum4linuxTool(BaseTool):
    metadata = ToolMetadata(
        name="enum4linux",
        description="enum4linux SMB/Windows 信息枚举工具，收集用户/共享/策略/密码策略等。",
        category=ToolCategory.PRIVILEGE_ESCALATION,
        tags=["enum4linux", "smb", "windows", "enumeration", "netbios"],
        references=["https://github.com/CiscoCX8/enum4linux"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标 IP", required=True),
            ToolParameter(name="options", type="string", description="选项: -U(用户) -S(共享) -P(密码策略) -G(组)", default="-U -S -P -G -a"),
        ],
        timeout_default=120.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["enum4linux"]
        cmd.extend(kwargs.get("options", "-U -S -P -G -a").split())
        cmd.append(kwargs.get("target", ""))
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        users, shares = [], []
        for line in raw_output.splitlines():
            m = re.match(r"\s*User:\s+(.+)", line)
            if m: users.append(m.group(1).strip())
            m = re.match(r"\s*Sharename\s+Type\s+Comment", line)
            if m:
                for l in raw_output.splitlines():
                    sm = re.match(r"\s*(\S+)\s+(disk|print)\s+(.*)", l)
                    if sm: shares.append({"name": sm.group(1), "type": sm.group(2), "comment": sm.group(3)})
        return {"users": users[:50], "shares": shares[:20]}
