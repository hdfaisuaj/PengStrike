"""
SQL注入检测技能 (sql_injection_detect.py)

执行流程:
1. 使用 sqlmap --batch --smart 自动检测注入点
2. 如找到注入点，尝试获取数据库信息（库名、表名、字段）
3. 汇总结果返回
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from roles_skills.base_skill import BaseSkill
from tools.base_tool import ToolResult

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


class SQLInjectionDetect(BaseSkill):
    name: str = "sql_injection_detect"
    description: str = "SQL注入检测与利用：自动检测注入点并获取数据库信息"
    parameters: dict = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标 URL（带参数的完整 URL）"},
            "data": {"type": "string", "description": "POST 请求体（可选）"},
        },
        "required": ["url"],
    }

    async def run(self, orchestrator: Orchestrator, **kwargs) -> ToolResult:
        url = kwargs.get("url", "")
        if not url:
            return ToolResult(success=False, error="缺少必填参数: url", tool_name=self.name)

        # 步骤 1: 检测注入点
        step1 = await orchestrator.call_tool(
            "sqlmap",
            url=url,
            args="--batch --smart --random-agent --level=2 --risk=2",
        )
        if not step1.success:
            return ToolResult(
                success=False,
                error=f"SQL注入检测失败: {step1.error}",
                duration=step1.duration,
                tool_name=self.name,
            )

        injection_info = self._parse_injection_info(step1.output)
        if not injection_info.get("injectable"):
            return ToolResult(
                success=True,
                output=f"目标 {url} 未发现可注入的 SQL 注入点。",
                duration=step1.duration,
                tool_name=self.name,
            )

        # 步骤 2: 获取数据库基本信息
        step2 = await orchestrator.call_tool(
            "sqlmap",
            url=url,
            args="--batch --dbs --random-agent",
        )

        dbs = []
        if step2.success:
            dbs = self._parse_databases(step2.output)

        return ToolResult(
            success=True,
            output=f"SQL注入检测完成\nURL: {url}\n可注入: 是\n注入类型: {injection_info.get('type', '未知')}\n数据库: {dbs or '获取失败'}",
            structured_data={
                "injectable": True,
                "injection_type": injection_info.get("type"),
                "parameter": injection_info.get("parameter"),
                "databases": dbs,
                "payload": injection_info.get("payload"),
            },
            duration=step1.duration + (step2.duration if step2.success else 0),
            tool_name=self.name,
        )

    @staticmethod
    def _parse_injection_info(sqlmap_output: str) -> dict:
        """解析 sqlmap 输出中的注入信息。"""
        info: dict = {"injectable": False}
        for line in sqlmap_output.splitlines():
            if "Parameter:" in line and "GET" in line:
                info["injectable"] = True
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "Parameter:" and i + 1 < len(parts):
                        info["parameter"] = parts[i + 1].strip("()")
            if "Type:" in line:
                info["type"] = line.split("Type:")[-1].strip()
            if "Payload:" in line:
                info["payload"] = line.split("Payload:")[-1].strip()[:200]
        return info

    @staticmethod
    def _parse_databases(sqlmap_output: str) -> list[str]:
        """解析 sqlmap 输出的数据库列表。"""
        dbs: list[str] = []
        in_section = False
        for line in sqlmap_output.splitlines():
            if "available databases" in line.lower():
                in_section = True
                continue
            if in_section:
                m = __import__("re").match(r"^\[\*\]\s+(\S+)", line)
                if m:
                    dbs.append(m.group(1))
        return dbs