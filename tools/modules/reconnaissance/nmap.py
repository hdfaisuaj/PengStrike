"""
Nmap 工具 (tools/modules/reconnaissance/nmap.py)

支持:
- 主机发现 (-sn)
- 端口扫描 (-sS/-sT/-sU)
- 服务版本探测 (-sV)
- 操作系统探测 (-O)
- NSE 脚本扫描 (-sC)
- 输出格式: -oX (XML), -oN (正常), -oG (grepable)
- 结构化解析: 优先解析 XML 输出，失败则正则解析
"""

from __future__ import annotations

import json
import re
import shlex
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolParameter, ToolResult


# ========================================================================
# Nmap 结构化输出模型
# ========================================================================
class NmapHost(BaseModel):
    ip: str
    status: str = "unknown"
    hostname: Optional[str] = None
    os: Optional[str] = None
    ports: List["NmapPort"] = Field(default_factory=list)
    mac: Optional[str] = None
    vendor: Optional[str] = None


class NmapPort(BaseModel):
    port_id: int
    protocol: str = "tcp"
    state: str = "unknown"
    service: Optional[str] = None
    version: Optional[str] = None
    product: Optional[str] = None
    extrainfo: Optional[str] = None
    scripts: Dict[str, str] = Field(default_factory=dict)


class NmapOutput(BaseModel):
    nmap_version: Optional[str] = None
    nmap_args: Optional[str] = None
    scan_date: Optional[str] = None
    scan_time: Optional[str] = None
    hosts_up: int = 0
    hosts_down: int = 0
    hosts_scanned: int = 0
    hosts: List[NmapHost] = Field(default_factory=list)


# 解决前向引用
NmapPort.model_rebuild()


# ========================================================================
# Nmap 工具
# ========================================================================
class NmapTool(BaseTool):
    """Nmap 网络扫描工具。"""

    metadata = ToolMetadata(
        name="nmap",
        description="网络扫描工具，支持主机发现、端口扫描、服务探测、OS探测、NSE脚本扫描。",
        category=ToolCategory.RECONNAISSANCE,
        tags=["scan", "network", "recon", "port", "service", "os"],
        version="7.94",
        references=["https://nmap.org/docs.html"],
        parameters=[
            ToolParameter(name="targets", type="string", description="目标（IP/CIDR/主机名）", required=True),
            ToolParameter(name="scan_type", type="string", description="扫描类型: syn/tcp/udp/ping", default="syn"),
            ToolParameter(name="ports", type="string", description="端口范围（如 1-1000,22,80)", default=""),
            ToolParameter(name="scripts", type="string", description="NSE 脚本（默认 -sC）", default="default"),
            ToolParameter(name="service_detect", type="boolean", description="启用服务版本探测", default=True),
            ToolParameter(name="os_detect", type="boolean", description="启用 OS 探测", default=False),
            ToolParameter(name="top_ports", type="integer", description="扫描最常见的 N 个端口", default=0),
            ToolParameter(name="timing", type="integer", description="时序级别 0-5（T0-T5）", default=4),
            ToolParameter(name="output_format", type="string", description="输出格式: xml/json/grepable/normal", default="xml"),
            ToolParameter(name="extra_args", type="string", description="额外参数", default=""),
        ],
        timeout_default=300.0,
    )

    async def run(self, **kwargs) -> ToolResult:
        targets = kwargs.get("targets", "")
        scan_type = kwargs.get("scan_type", "syn")
        ports = kwargs.get("ports", "")
        scripts = kwargs.get("scripts", "default")
        service_detect = kwargs.get("service_detect", True)
        os_detect = kwargs.get("os_detect", False)
        top_ports = kwargs.get("top_ports", 0)
        timing = kwargs.get("timing", 4)
        output_format = kwargs.get("output_format", "xml")
        extra_args = kwargs.get("extra_args", "")

        cmd = ["nmap"]

        scan_map = {"syn": "-sS", "tcp": "-sT", "udp": "-sU", "ping": "-sn"}
        cmd.append(scan_map.get(scan_type, "-sS"))

        if ports:
            cmd.extend(["-p", ports])
        elif top_ports > 0:
            cmd.extend(["--top-ports", str(top_ports)])

        if service_detect:
            cmd.append("-sV")

        if os_detect:
            cmd.append("-O")

        if scripts and scripts != "none":
            cmd.extend(["--script", scripts])

        cmd.extend(["-T", str(timing)])

        if output_format == "xml":
            cmd.extend(["-oX", "-"])
        elif output_format == "json":
            cmd.extend(["-oJ", "-"])
        elif output_format == "grepable":
            cmd.extend(["-oG", "-"])
        else:
            cmd.extend(["-oN", "-"])

        if extra_args:
            cmd.extend(shlex.split(extra_args))

        cmd.append(targets)

        return await self._run_cmd(cmd, timeout=self.metadata.timeout_default)

    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        # 方法1: 尝试 JSON 解析（nmap --oJ）
        try:
            for line in raw_output.splitlines():
                if line.startswith("{"):
                    data = json.loads(line)
                    return self._parse_nmap_json(data)
        except Exception:
            pass

        # 方法2: 尝试 XML 解析（nmap -oX -）
        try:
            root = ET.fromstring(raw_output)
            return self._parse_nmap_xml(root)
        except Exception:
            pass

        # 方法3: 降级为正则解析
        return self._parse_nmap_regex(raw_output)

    def _parse_nmap_xml(self, root: ET.Element) -> Dict[str, Any]:
        """解析 Nmap XML 输出。"""
        nmaprun = root
        result: Dict[str, Any] = {
            "hosts": [],
            "hosts_up": 0,
            "hosts_down": 0,
        }

        for attr in ["nmapversion", "args", "start", "end"]:
            v = nmaprun.get(attr)
            if v:
                result[attr] = v

        for host_el in nmaprun.findall("host"):
            host: Dict[str, Any] = {"ports": []}

            # 状态
            status_el = host_el.find("status")
            if status_el is not None:
                host["status"] = status_el.get("state", "unknown")
                if host["status"] == "up":
                    result["hosts_up"] = result.get("hosts_up", 0) + 1

            # 地址
            for addr_el in host_el.findall("address"):
                addr_type = addr_el.get("addrtype", "")
                addr = addr_el.get("addr", "")
                if addr_type == "ipv4" or addr_type == "ipv6":
                    host["ip"] = addr
                elif addr_type == "mac":
                    host["mac"] = addr
                    host["vendor"] = addr_el.get("vendor")

            # 主机名
            hostnames_el = host_el.find("hostnames")
            if hostnames_el is not None:
                hn_el = hostnames_el.find("hostname")
                if hn_el is not None:
                    host["hostname"] = hn_el.get("name")

            # OS
            os_el = host_el.find("osmatch")
            if os_el is not None:
                host["os"] = os_el.get("name")

            # 端口
            ports_el = host_el.find("ports")
            if ports_el is not None:
                for port_el in ports_el.findall("port"):
                    port: Dict[str, Any] = {
                        "port_id": int(port_el.get("portid", 0)),
                        "protocol": port_el.get("protocol", "tcp"),
                    }
                    state_el = port_el.find("state")
                    if state_el is not None:
                        port["state"] = state_el.get("state", "unknown")

                    svc_el = port_el.find("service")
                    if svc_el is not None:
                        port["service"] = svc_el.get("name")
                        port["product"] = svc_el.get("product")
                        port["version"] = svc_el.get("version")
                        port["extrainfo"] = svc_el.get("extrainfo")

                    # NSE 脚本结果
                    scripts: Dict[str, str] = {}
                    for script_el in port_el.findall("script"):
                        sid = script_el.get("id", "")
                        sout = script_el.get("output", "")
                        scripts[sid] = sout
                    port["scripts"] = scripts

                    host["ports"].append(port)

            result["hosts"].append(host)

        return result

    def _parse_nmap_json(self, data: Dict) -> Dict[str, Any]:
        """解析 Nmap JSON 输出（简化）。"""
        hosts = []
        for h in data.get("nmaprun", {}).get("host", []):
            host: Dict[str, Any] = {}
            addrs = h.get("address", [])
            if isinstance(addrs, dict):
                addrs = [addrs]
            for addr in addrs:
                if addr.get("addrtype") in ("ipv4", "ipv6"):
                    host["ip"] = addr.get("addr")
                elif addr.get("addrtype") == "mac":
                    host["mac"] = addr.get("addr")
                    host["vendor"] = addr.get("vendor")

            host["status"] = h.get("status", {}).get("state", "unknown")
            host["hostname"] = (h.get("hostnames", {}) or {}).get("hostname", [{}])[0].get("name")

            ports = []
            port_list = h.get("ports", {}).get("port", [])
            if isinstance(port_list, dict):
                port_list = [port_list]
            for p in port_list:
                port: Dict[str, Any] = {
                    "port_id": int(p.get("portid", 0)),
                    "protocol": p.get("protocol", "tcp"),
                    "state": p.get("state", {}).get("state", "unknown"),
                }
                svc = p.get("service", {})
                if svc:
                    port["service"] = svc.get("name")
                    port["product"] = svc.get("product")
                    port["version"] = svc.get("version")
                ports.append(port)
            host["ports"] = ports
            hosts.append(host)
        return {"hosts": hosts, "hosts_up": len([h for h in hosts if h.get("status") == "up"])}

    def _parse_nmap_regex(self, text: str) -> Dict[str, Any]:
        """正则解析（最后的兜底方案）。"""
        result: Dict[str, Any] = {"hosts": [], "raw_lines": []}
        current_host: Dict[str, Any] = {}
        current_ports: List[Dict] = []

        for line in text.splitlines():
            # 解析状态行
            host_match = re.match(r"Host: ([\d.]+)", line)
            if host_match:
                if current_host:
                    current_host["ports"] = current_ports
                    result["hosts"].append(current_host)
                current_host = {"ip": host_match.group(1), "ports": []}
                current_ports = []
                if "Status: Up" in line:
                    current_host["status"] = "up"
                continue

            # 解析端口行 (格式: PORT   STATE SERVICE VERSION)
            port_match = re.match(r"(\d+)/(tcp|udp)\s+(\w+)\s+(\S+)(.*)", line)
            if port_match and current_host:
                port: Dict[str, Any] = {
                    "port_id": int(port_match.group(1)),
                    "protocol": port_match.group(2),
                    "state": port_match.group(3),
                    "service": port_match.group(4),
                }
                extra = port_match.group(5).strip()
                if extra:
                    port["version"] = extra
                current_ports.append(port)
                continue

        # Hosts up 统计（放到循环外，避免重复计算）
        up_match = re.search(r"(\d+) host(?:s)? up", text)
        if up_match:
            result["hosts_up"] = int(up_match.group(1))

        if current_host:
            current_host["ports"] = current_ports
            result["hosts"].append(current_host)

        return result
