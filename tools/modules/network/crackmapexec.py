"""CrackMapExec 内网域渗透工具"""
from __future__ import annotations
import re, shlex
from typing import Any, Dict, List, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class CrackmapexecTool(BaseTool):
    metadata = ToolMetadata(
        name="crackmapexec",
        description="CrackMapExec 内网域渗透工具，支持 SMB/WinRM/LDAP/SSH 协议的信息收集和横向移动。",
        category=ToolCategory.LATERAL_MOVEMENT,
        tags=["cme", "crackmapexec", "smb", "winrm", "ldap", "lateral", "domain"],
        version="5.4",
        references=["https://github.com/PorLaCola25/CrackMapExec"],
        parameters=[
            ToolParameter(name="target", type="string", description="目标 IP/CIDR/主机名列表文件", required=True),
            ToolParameter(name="protocol", type="string", description="协议: smb/winrm/ldap/ssh", default="smb"),
            ToolParameter(name="action", type="string", description="操作: info/users/shares/sessions/lootsam", default="info"),
            ToolParameter(name="username", type="string", description="用户名", default=""),
            ToolParameter(name="password", type="string", description="密码", default=""),
            ToolParameter(name="hash", type="string", description="NTLM Hash", default=""),
            ToolParameter(name="dc", type="string", description="域控制器 IP", default=""),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["crackmapexec", kwargs.get("protocol", "smb")]
        target = kwargs.get("target", "")
        action = kwargs.get("action", "info")
        cmd.extend(["-u", kwargs.get("username", "guest")])

        pwd = kwargs.get("password", "")
        hsh = kwargs.get("hash", "")
        if hsh:
            cmd.extend(["-H", hsh])
        elif pwd:
            cmd.extend(["-p", pwd])

        if kwargs.get("dc"):
            cmd.extend(["-d", kwargs.get("dc", "")])

        cmd.append(target)

        if action == "info":
            cmd.append("--local-auth")
        elif action == "users":
            cmd.extend(["-u"])
        elif action == "shares":
            cmd.append("--shares")
        elif action == "sam":
            cmd.append("--sam")

        extra = kwargs.get("extra_args", "")
        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        results = []
        for line in raw_output.splitlines():
            if line.strip() and not line.startswith("{"):
                parts = re.split(r"\s{2,}", line.strip())
                if len(parts) >= 3:
                    results.append({"host": parts[0], "status": parts[1], "details": " ".join(parts[2:])})
        return {"hosts": results, "count": len(results)}
