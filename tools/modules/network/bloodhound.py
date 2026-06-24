"""BloodHound Python (Sharphound) 域渗透枚举"""
from __future__ import annotations
import json, re
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class BloodhoundTool(BaseTool):
    metadata = ToolMetadata(
        name="bloodhound",
        description="BloodHound Python (Sharphound) 域环境分析工具，收集 AD 攻击路径信息并输出 JSON。",
        category=ToolCategory.LATERAL_MOVEMENT,
        tags=["bloodhound", "sharphound", "ad", "ldap", "domain", "attack_path"],
        references=["https://github.com/BloodHoundAD/BloodHound"],
        parameters=[
            ToolParameter(name="domain", type="string", description="目标域名", required=True),
            ToolParameter(name="username", type="string", description="用户名", default=""),
            ToolParameter(name="password", type="string", description="密码", default=""),
            ToolParameter(name="domain_controller", type="string", description="域控制器", default=""),
            ToolParameter(name="collection_methods", type="string", description="收集方法: Default/Sessions/LoggedOn/Acl/Group", default="Default"),
            ToolParameter(name="output_dir", type="string", description="输出目录", default="/tmp/bloodhound"),
        ],
        timeout_default=600.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["python3", "-m", "bloodhound"]
        domain = kwargs.get("domain", "")
        username = kwargs.get("username", "")
        password = kwargs.get("password", "")
        dc = kwargs.get("domain_controller", "")
        collection = kwargs.get("collection_methods", "Default")
        out_dir = kwargs.get("output_dir", "/tmp/bloodhound")

        cmd.extend(["-d", domain])
        if username:
            cmd.extend(["-u", username])
        if password:
            cmd.extend(["-p", password])
        if dc:
            cmd.extend(["-dc", dc])
        cmd.extend(["-c", collection])
        cmd.extend(["-o", out_dir])

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        completed = "completed" in raw_output.lower() or "finished" in raw_output.lower()
        return {"completed": completed, "length": len(raw_output)}
