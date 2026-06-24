"""LinPEAS/Linux 提权枚举脚本"""
from __future__ import annotations
import os, shlex
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class LinpeasTool(BaseTool):
    metadata = ToolMetadata(
        name="linpeas",
        description="LinPEAS Linux 提权辅助枚举脚本，全面检查 SUID/Sudo/Cron/NFS/容器/内核漏洞等提权路径。",
        category=ToolCategory.PRIVILEGE_ESCALATION,
        tags=["linpeas", "privesc", "linux", "enumeration", "privilege"],
        references=["https://github.com/carlospolop/PEASS-ng"],
        requires_root=False,
        parameters=[
            ToolParameter(name="script_path", type="string", description="linpeas.sh 脚本路径（默认自动搜索常见位置）", default=""),
            ToolParameter(name="output_file", type="string", description="输出文件路径", default=""),
            ToolParameter(name="color", type="string", description="输出颜色: 0(无)/1(暗)/2(亮)", default="2"),
            ToolParameter(name="options", type="string", description="额外选项: quiet/notcolor/superquiet/json/sh", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        options = kwargs.get("options", "")
        # 使用用户定义的脚本路径，缺省优先从常见位置查找（避免硬编码 /tmp）
        script_path = kwargs.get("script_path", "")
        if not script_path:
            for candidate in ["linpeas.sh", "privesc.sh",
                              os.path.join(os.path.expanduser("~"), "linpeas.sh"),
                              "/opt/privesc/linpeas.sh", "/tmp/linpeas.sh"]:
                if os.path.exists(candidate):
                    script_path = candidate
                    break
        if not script_path:
            script_path = "linpeas.sh"

        cmd = ["bash", script_path]
        if options:
            cmd.extend(shlex.split(options))
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        findings = []
        for line in raw_output.splitlines():
            if any(kw in line for kw in ["SUID", "sudo", "Crons", "Writable", "VULN", "Root", "[!]", "CVE"]):
                findings.append(line.strip())
        return {"findings_count": len(findings), "findings": findings[:100]} if findings else None
