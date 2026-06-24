"""Impacket Python 攻击工具集（简化包装）"""
from __future__ import annotations
import shlex
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class ImpacketTool(BaseTool):
    metadata = ToolMetadata(
        name="impacket",
        description="Impacket 工具集 Python 包装，支持 secretsdump/psexec/wmiexec/ntlmrelayx/smbclient 等。",
        category=ToolCategory.LATERAL_MOVEMENT,
        tags=["impacket", "secretsdump", "wmiexec", "psexec", "lateral", "smb"],
        references=["https://github.com/fortra/impacket"],
        parameters=[
            ToolParameter(name="script", type="string", description="Impacket 脚本名: secretsdump/psexec/wmiexec/ntlmrelayx/smbclient", required=True),
            ToolParameter(name="target", type="string", description="目标", required=True),
            ToolParameter(name="username", type="string", description="用户名", default=""),
            ToolParameter(name="password", type="string", description="密码", default=""),
            ToolParameter(name="domain", type="string", description="域名", default=""),
            ToolParameter(name="hash", type="string", description="NTLM Hash", default=""),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        script = kwargs.get("script", "")
        target = kwargs.get("target", "")
        username = kwargs.get("username", "")
        password = kwargs.get("password", "")
        domain = kwargs.get("domain", "")
        hsh = kwargs.get("hash", "")
        extra = kwargs.get("extra_args", "")

        cmd = ["python3", "-m", f"impacket.examples.{script}"]
        # 凭据拼接: domain/username@target（domain 可为空）
        cred_target = f"{domain}/{username}@{target}" if domain else f"{username}@{target}"
        cmd.append(cred_target)
        if hsh:
            cmd.append("-hashes")
            cmd.append(f":{hsh}")
        elif password:
            cmd.append("-passwords")
            cmd.append(password)

        if extra:
            cmd.extend(shlex.split(extra))

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        return {"length": len(raw_output), "has_hashes": ":" in raw_output and len(raw_output) < 5000}
