"""Evil-WinRM 远程管理利用工具"""
from __future__ import annotations
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class EvilWinRMTool(BaseTool):
    metadata = ToolMetadata(
        name="evil-winrm",
        description="Evil-WinRM Windows 远程管理利用工具，通过 WinRM 获取交互式 Shell（支持 Pass-the-Hash）。",
        category=ToolCategory.LATERAL_MOVEMENT,
        tags=["evil-winrm", "winrm", "lateral", "shell", "pth"],
        references=["https://github.com/Hackplayers/evil-winrm"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标 IP", required=True),
            ToolParameter(name="username", type="string", description="用户名", default=""),
            ToolParameter(name="password", type="string", description="密码", default=""),
            ToolParameter(name="hash", type="string", description="NTLM Hash", default=""),
            ToolParameter(name="command", type="string", description="要执行的命令", default=""),
        ],
        timeout_default=30.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        target = kwargs.get("target", "")
        user = kwargs.get("username", "")
        pwd = kwargs.get("password", "")
        hsh = kwargs.get("hash", "")
        cmd_str = kwargs.get("command", "")

        cmd = ["evil-winrm", "-i", target]
        if hsh:
            cmd.extend(["-u", user, "-H", hsh])
        elif pwd:
            cmd.extend(["-u", user, "-p", pwd])

        if cmd_str:
            cmd.extend(["-x", cmd_str])

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        return {"has_shell": any(kw in raw_output for kw in ["PS", "C:\\", "Microsoft"])}
