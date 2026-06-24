"""
快速端口扫描技能 (quick_port_scan.py)

执行流程:
1. TCP 1-10000 端口快速扫描 (nmap -T4 -Pn)
2. 服务识别与版本检测 (nmap -sC -sV -Pn)
3. 合并结果返回
"""
from __future__ import annotations
from typing import TYPE_CHECKING
from pydantic import Field
from roles_skills.base_skill import BaseSkill
from tools.base_tool import ToolResult

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


class QuickPortScan(BaseSkill):
    name: str = "quick_port_scan"
    description: str = "快速端口扫描：TCP 1-10000 端口扫描 + 服务识别 + 版本检测"
    parameters: dict = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "目标 IP 地址"},
        },
        "required": ["target"],
    }

    async def run(self, orchestrator: Orchestrator, **kwargs) -> ToolResult:
        target = kwargs.get("target", "")
        if not target:
            return ToolResult(success=False, error="缺少必填参数: target", tool_name=self.name)

        # 步骤 1: TCP 1-10000 快速扫描
        step1 = await orchestrator.call_tool(
            "nmap",
            target=target,
            ports="1-10000",
            args="-T4 -Pn",
        )
        if not step1.success:
            return ToolResult(
                success=False,
                error=f"端口扫描失败: {step1.error}",
                duration=step1.duration,
                tool_name=self.name,
            )

        open_ports = self._extract_open_ports(step1.output)
        if not open_ports:
            return ToolResult(
                success=True,
                output=f"目标 {target} 在 1-10000 端口范围内未发现开放端口。",
                duration=step1.duration,
                tool_name=self.name,
            )

        # 步骤 2: 服务识别与版本检测
        ports_str = ",".join(str(p) for p in open_ports)
        step2 = await orchestrator.call_tool(
            "nmap",
            target=target,
            ports=ports_str,
            args="-sC -sV -Pn",
        )
        if not step2.success:
            return ToolResult(
                success=True,
                output=f"端口扫描完成，但服务识别失败: {step2.error}\n开放端口: {open_ports}",
                structured_data={"open_ports": open_ports, "services": []},
                duration=step1.duration + step2.duration,
                tool_name=self.name,
            )

        services = self._extract_services(step2.output)

        return ToolResult(
            success=True,
            output=f"快速端口扫描完成\n目标: {target}\n开放端口 ({len(open_ports)}): {open_ports}\n服务信息: {services}",
            structured_data={"open_ports": open_ports, "services": services},
            duration=step1.duration + step2.duration,
            tool_name=self.name,
        )

    @staticmethod
    def _extract_open_ports(nmap_output: str) -> list[int]:
        """从 nmap 输出中提取开放端口号列表。"""
        import re
        ports: list[int] = []
        for line in nmap_output.splitlines():
            m = re.match(r"^(\d+)/tcp\s+open", line)
            if m:
                ports.append(int(m.group(1)))
        return ports

    @staticmethod
    def _extract_services(nmap_output: str) -> list[dict]:
        """从 nmap -sC -sV 输出中提取服务信息。"""
        import re
        services: list[dict] = []
        for line in nmap_output.splitlines():
            m = re.match(r"^(\d+)/tcp\s+open\s+(\S+)\s+(.+)$", line)
            if m:
                services.append({
                    "port": int(m.group(1)),
                    "protocol": "tcp",
                    "service": m.group(2),
                    "version": m.group(3).strip(),
                })
        return services