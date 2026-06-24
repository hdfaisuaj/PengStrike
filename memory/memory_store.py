"""
跨会话记忆存储 (memory/memory_store.py)

功能:
- 按目标 IP 存储历史扫描发现（端口、服务版本、目录路径、凭证、漏洞信息）
- 新会话对同 IP 自动注入历史发现，避免重复扫描
- 使用 SQLite 持久化
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional


# 数据库路径（放在项目根目录的 memory/ 下）
MEMORY_DB_DIR = os.path.dirname(os.path.abspath(__file__))
MEMORY_DB_PATH = os.path.join(MEMORY_DB_DIR, "target_memory.db")


class TargetMemoryStore:
    """按目标 IP 的记忆存储。线程安全。"""

    _instance: Optional["TargetMemoryStore"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "TargetMemoryStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_db()
        return cls._instance

    def _init_db(self) -> None:
        os.makedirs(MEMORY_DB_DIR, exist_ok=True)
        self._conn = sqlite3.connect(MEMORY_DB_PATH, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS target_memory (
                target_ip TEXT PRIMARY KEY,
                last_seen REAL NOT NULL,
                nmap_summary TEXT DEFAULT '',
                open_ports TEXT DEFAULT '[]',
                web_dirs TEXT DEFAULT '[]',
                services TEXT DEFAULT '[]',
                credentials TEXT DEFAULT '[]',
                cves TEXT DEFAULT '[]',
                notes TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def _conn(self):
        return self._conn

    # ------------------------------------------------------------------
    # 保存发现
    # ------------------------------------------------------------------
    def save_findings(self, target_ip: str, findings: Dict[str, Any]) -> None:
        """保存对目标 IP 的扫描发现。"""
        import time
        now = time.time()

        # 先读取已有记录，再合并
        existing = self.get_memory(target_ip)

        def _merge_list(old: list, new: list) -> str:
            merged = {json.dumps(item, sort_keys=True) for item in (old or [])}
            for item in (new or []):
                merged.add(json.dumps(item, sort_keys=True))
            return json.dumps([json.loads(m) for m in merged], ensure_ascii=False)

        self._conn.execute("""
            INSERT OR REPLACE INTO target_memory
            (target_ip, last_seen, nmap_summary, open_ports, web_dirs, services, credentials, cves, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            target_ip,
            now,
            findings.get("nmap_summary", existing.get("nmap_summary", "")),
            _merge_list(
                json.loads(existing.get("open_ports", "[]")),
                findings.get("open_ports", [])
            ),
            _merge_list(
                json.loads(existing.get("web_dirs", "[]")),
                findings.get("web_dirs", [])
            ),
            _merge_list(
                json.loads(existing.get("services", "[]")),
                findings.get("services", [])
            ),
            _merge_list(
                json.loads(existing.get("credentials", "[]")),
                findings.get("credentials", [])
            ),
            json.dumps(findings.get("cves", []), ensure_ascii=False),
            findings.get("notes", existing.get("notes", "")),
        ))
        self._conn.commit()

    # ------------------------------------------------------------------
    # 读取记忆
    # ------------------------------------------------------------------
    def get_memory(self, target_ip: str) -> Dict[str, Any]:
        """读取目标 IP 的历史发现。"""
        cursor = self._conn.execute(
            "SELECT * FROM target_memory WHERE target_ip = ?", (target_ip,)
        )
        row = cursor.fetchone()
        if row is None:
            return {}
        return dict(row)

    # ------------------------------------------------------------------
    # 生成提示词注入文本
    # ------------------------------------------------------------------
    def build_injection_prompt(self, target_ip: str) -> str:
        """生成要注入到 AI 对话中的历史记忆文本。"""
        mem = self.get_memory(target_ip)
        if not mem:
            return ""

        parts = ["【历史记忆 — 该目标已有扫描记录】"]

        # 端口
        open_ports = json.loads(mem.get("open_ports", "[]"))
        if open_ports:
            port_lines = "\n".join(f"  - {p}" for p in open_ports)
            parts.append(f"历史上已发现的开放端口：\n{port_lines}")

        # Web 目录
        web_dirs = json.loads(mem.get("web_dirs", "[]"))
        if web_dirs:
            dir_lines = "\n".join(f"  - {d}" for d in web_dirs)
            parts.append(f"历史上已发现的 Web 目录：\n{dir_lines}")

        # 凭证
        credentials = json.loads(mem.get("credentials", "[]"))
        if credentials:
            cred_lines = "\n".join(
                f"  - [{c.get('type', '?')}] {c.get('value', '?')}" for c in credentials
            )
            parts.append(f"历史上已发现的凭证：\n{cred_lines}")

        # 服务版本
        services = json.loads(mem.get("services", "[]"))
        if services:
            svc_lines = "\n".join(f"  - {s}" for s in services)
            parts.append(f"历史上发现的服务：\n{svc_lines}")

        # CVE
        cves = json.loads(mem.get("cves", "[]"))
        if cves:
            cve_lines = "\n".join(f"  - {c}" for c in cves)
            parts.append(f"历史上发现的 CVE：\n{cve_lines}")

        # nmap 摘要
        nmap_summary = mem.get("nmap_summary", "")
        if nmap_summary:
            parts.append(f"nmap 扫描摘要：\n{nmap_summary}")

        parts.append(
            "【提示】以上信息来自历史扫描，可能已过期。你应该优先直接利用这些已有信息，"
            "而不是重新扫描确认。如果利用失败，再考虑重新扫描。"
        )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # 从会话结果中提取发现
    # ------------------------------------------------------------------
    def extract_findings_from_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """从会话执行步骤中提取发现。"""
        findings = {
            "open_ports": [],
            "web_dirs": [],
            "services": [],
            "credentials": [],
            "cves": [],
            "nmap_summary": "",
            "notes": "",
        }

        steps = session_data.get("execution_steps", [])
        for step in steps:
            args = step.get("tool_args", {})
            cmd = str(args.get("command", ""))
            output = step.get("tool_output_summary", "")

            # 提取 nmap 结果中的开放端口
            if "nmap" in cmd and "/tcp" in output:
                import re
                ports = re.findall(r"(\d+/tcp)\s+(\S+)\s+(.+?)(?:\n|$)", output)
                for port, state, service in ports:
                    entry = f"{port} {state} {service.strip()}"
                    if entry not in findings["open_ports"]:
                        findings["open_ports"].append(entry)

            # 提取 dirsearch / gobuster 发现的目录
            if ("dirsearch" in cmd or "gobuster" in cmd or "dirb" in cmd) and output:
                import re
                dirs = re.findall(r"(/\S+)\s+\(Status:\s*\d+\)", output)
                for d in dirs:
                    if d not in findings["web_dirs"]:
                        findings["web_dirs"].append(d)

            # 提取凭证
            if "hydra" in cmd and "password" in output.lower():
                match = re.search(r"(\S+):(\S+)\s+\[(\S+)\]", output)
                if match:
                    findings["credentials"].append({
                        "type": "login",
                        "value": f"{match.group(1)}/{match.group(2)}",
                        "source": cmd[:100],
                    })

        # 设置 nmap 摘要
        if findings["open_ports"]:
            findings["nmap_summary"] = "\n".join(findings["open_ports"][:20])

        return findings


# 全局单例
_target_memory_store: Optional[TargetMemoryStore] = None


def get_target_memory_store() -> TargetMemoryStore:
    global _target_memory_store
    if _target_memory_store is None:
        _target_memory_store = TargetMemoryStore()
    return _target_memory_store
