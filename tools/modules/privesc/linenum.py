"""LinEnum Linux 本地信息枚举"""
from __future__ import annotations
from typing import Any, Dict, Optional
from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


class LinenumTool(BaseTool):
    metadata = ToolMetadata(
        name="linenum",
        description="LinEnum Linux 本地枚举脚本，检查系统信息/网络/用户/计划任务/服务等。",
        category=ToolCategory.PRIVILEGE_ESCALATION,
        tags=["linenum", "enumeration", "linux", "local"],
        references=["https://github.com/rebootuser/LinEnum"],
        parameters=[
            ToolParameter(name="output_file", type="string", description="输出文件", default="/tmp/linenum_output.txt"),
            ToolParameter(name="options", type="string", description="选项: -s(敏感) -k(密码) -r(可靠)", default="-s -k"),
        ],
        timeout_default=120.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        cmd = ["bash", "/tmp/LinEnum.sh"]
        options = kwargs.get("options", "-s -k").split()
        cmd.extend(options)
        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        return {"length": len(raw_output), "preview": raw_output[:1000]}
