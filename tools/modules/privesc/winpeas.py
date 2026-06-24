"""WinPEAS Windows 提权枚举脚本"""
from __future__ import annotations
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class WinpeasTool(BaseTool):
    metadata = ToolMetadata(
        name="winpeas",
        description="WinPEAS Windows 提权辅助枚举脚本，检查服务/注册表/令牌/补丁/权限等。",
        category=ToolCategory.PRIVILEGE_ESCALATION,
        tags=["winpeas", "privesc", "windows", "enumeration"],
        references=["https://github.com/carlospolop/PEASS-ng"],
        supported_platforms=["windows"],
        requires_root=False,
        parameters=[
            ToolParameter(name="output_file", type="string", description="输出文件路径", default="C:\\temp\\winpeas_output.txt"),
            ToolParameter(name="options", type="string", description="额外选项: notcolor quiet systeminfo", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        options = kwargs.get("options", "")
        ps_cmd = "C:\\temp\\winpeas.exe"
        if options:
            ps_cmd = f"C:\\temp\\winpeas.exe {options}"
        cmd = ["powershell", "-NoProfile", "-Command", ps_cmd]
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        findings = [l.strip() for l in raw_output.splitlines() if any(kw in l for kw in ["VULN", "WRITABLE", "SERVICE", "CVE", "AlwaysInstallElevated"])]
        return {"findings_count": len(findings), "findings": findings[:100]}
