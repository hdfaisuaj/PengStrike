"""
智能错误处理中间件 - Error Translation Middleware
将技术栈原生异常转换为用户友好提示 + 可执行解决方案
基于异常分类知识库实现智能匹配
"""
from __future__ import annotations
import csv
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from .base import IMiddleware, MiddlewareContext
logger = logging.getLogger(__name__)
@dataclass
class ErrorKnowledge:
    """错误知识条目"""
    error_id: str
    category: str
    tool_name: str
    error_pattern: str
    user_friendly_message: str
    solution: str
    severity: str
    fix_time_estimate: int
    auto_fixable: bool
    match_count: int = 0
@dataclass
class TranslatedError:
    """翻译后的错误信息"""
    original_error: str
    matched_pattern: Optional[str]
    user_message: str
    solutions: List[str]
    severity: str
    error_id: Optional[str]
    category: str
    fix_time_estimate: int
    auto_fixable: bool
    timestamp: datetime = field(default_factory=datetime.now)
    suggestion: Optional[str] = None
class ErrorKnowledgeBase:
    """错误知识库 - 单例模式"""
    _instance: Optional['ErrorKnowledgeBase'] = None
    _initialized: bool = False
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __init__(self):
        if not self._initialized:
            self.knowledge_base: List[ErrorKnowledge] = []
            self._pattern_cache: Dict[str, re.Pattern] = {}
            self._load_knowledge_base()
            self._initialized = True
    def _load_knowledge_base(self) -> None:
        """加载CSV格式的错误知识库"""
        csv_path = Path(__file__).parent.parent / "data" / "error_knowledge" / "error_classification.csv"
        if not csv_path.exists():
            logger.warning(f"Error knowledge base not found: {csv_path}")
            return
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    knowledge = ErrorKnowledge(
                        error_id=row['error_id'],
                        category=row['category'],
                        tool_name=row['tool_name'],
                        error_pattern=row['error_pattern'],
                        user_friendly_message=row['user_friendly_message'],
                        solution=row['solution'],
                        severity=row['severity'],
                        fix_time_estimate=int(row['fix_time_estimate']),
                        auto_fixable=row['auto_fixable'].lower() == 'true'
                    )
                    self.knowledge_base.append(knowledge)
                    # 预编译正则
                    self._pattern_cache[knowledge.error_id] = re.compile(
                        re.escape(knowledge.error_pattern).replace(r'\*', '.*'),
                        re.IGNORECASE
                    )
            logger.info(f"Loaded {len(self.knowledge_base)} error knowledge entries")
        except Exception as e:
            logger.error(f"Failed to load error knowledge base: {e}")
    def match_error(self, error_message: str) -> Optional[ErrorKnowledge]:
        """匹配错误信息到知识库"""
        if not error_message:
            return None
        error_str = str(error_message)
        best_match: Optional[ErrorKnowledge] = None
        best_score = 0
        for knowledge in self.knowledge_base:
            pattern = knowledge.error_pattern
            # 精确子串匹配
            if pattern.lower() in error_str.lower():
                score = len(pattern)  # 越长的匹配越精确
                if score > best_score:
                    best_score = score
                    best_match = knowledge
            # 正则匹配（用于包含通配符的模式）
            elif '*' in pattern:
                regex = self._pattern_cache.get(knowledge.error_id)
                if regex and regex.search(error_str):
                    score = len(pattern)
                    if score > best_score:
                        best_score = score
                        best_match = knowledge
        if best_match:
            best_match.match_count += 1
        return best_match
    def get_statistics(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        categories: Dict[str, int] = {}
        severities: Dict[str, int] = {}
        tools: Dict[str, int] = {}
        total_matches = 0
        for k in self.knowledge_base:
            categories[k.category] = categories.get(k.category, 0) + 1
            severities[k.severity] = severities.get(k.severity, 0) + 1
            tools[k.tool_name] = tools.get(k.tool_name, 0) + 1
            total_matches += k.match_count
        return {
            'total_entries': len(self.knowledge_base),
            'total_matches': total_matches,
            'categories': categories,
            'severities': severities,
            'top_tools': dict(sorted(tools.items(), key=lambda x: x[1], reverse=True)[:10])
        }
    def get_by_id(self, error_id: str) -> Optional[ErrorKnowledge]:
        """根据错误ID获取知识"""
        for k in self.knowledge_base:
            if k.error_id == error_id:
                return k
        return None
class ErrorTranslator:
    """错误翻译器 - 将技术错误转换为用户友好信息"""
    def __init__(self):
        self.kb = ErrorKnowledgeBase()
    def translate(self, error: Exception | str, context: Optional[Dict[str, Any]] = None) -> TranslatedError:
        """翻译错误信息"""
        error_str = str(error) if isinstance(error, Exception) else str(error)
        original_type = type(error).__name__ if isinstance(error, Exception) else "Unknown"
        # 匹配知识库
        matched = self.kb.match_error(error_str)
        if matched:
            solutions = [s.strip() for s in matched.solution.split(';') if s.strip()]
            return TranslatedError(
                original_error=f"{original_type}: {error_str}",
                matched_pattern=matched.error_pattern,
                user_message=matched.user_friendly_message,
                solutions=solutions,
                severity=matched.severity,
                error_id=matched.error_id,
                category=matched.category,
                fix_time_estimate=matched.fix_time_estimate,
                auto_fixable=matched.auto_fixable,
                suggestion=self._generate_suggestion(matched, solutions)
            )
        # 未匹配到的通用处理
        return self._generic_translation(error_str, original_type, context)
    def _generic_translation(self, error_str: str, error_type: str, context: Optional[Dict]) -> TranslatedError:
        """通用错误翻译"""
        user_message = f"执行过程中遇到问题: {error_type}"
        solutions = [
            "检查输入参数是否正确",
            "查看详细日志了解更多信息",
            "确认网络连接正常",
            "如问题持续请联系技术支持"
        ]
        # 基于错误类型的启发式翻译
        if 'timeout' in error_str.lower():
            user_message = "操作超时，请检查网络连接或目标服务器状态"
            solutions = ["增加超时时间", "检查目标服务器是否在线", "重试操作"]
        elif 'permission' in error_str.lower() or 'denied' in error_str.lower():
            user_message = "权限不足，无法执行此操作"
            solutions = ["使用sudo或以root用户执行", "检查文件/目录访问权限", "确认安全策略设置"]
        elif 'not found' in error_str.lower() or 'no such' in error_str.lower():
            user_message = "找不到指定的资源或文件"
            solutions = ["检查路径是否正确", "确认文件已存在", "使用绝对路径"]
        elif 'connection' in error_str.lower():
            user_message = "网络连接出现问题"
            solutions = ["检查网络连接", "确认目标服务运行正常", "检查防火墙设置"]
        return TranslatedError(
            original_error=f"{error_type}: {error_str}",
            matched_pattern=None,
            user_message=user_message,
            solutions=solutions,
            severity="WARNING",
            error_id=None,
            category="General",
            fix_time_estimate=5,
            auto_fixable=False,
            suggestion=None
        )
    def _generate_suggestion(self, matched: ErrorKnowledge, solutions: List[str]) -> Optional[str]:
        """生成操作建议"""
        if matched.auto_fixable and solutions:
            return f"💡 建议优先尝试: {solutions[0]}"
        return None
class ErrorTranslationMiddleware(IMiddleware):
    """
    异常翻译中间件
    在响应阶段自动翻译技术错误为用户友好信息
    """
    def __init__(self):
        super().__init__(name="error_translation", priority=10, enabled=True)
        self.translator = ErrorTranslator()
        self.error_history: List[TranslatedError] = []
    async def process_request(self, context: MiddlewareContext) -> None:
        """请求处理 - 暂存上下文信息"""
        pass
    async def process_response(self, context: MiddlewareContext) -> None:
        """响应处理 - 翻译错误信息"""
        error = context.get_error()
        if error:
            # 翻译错误
            translated = self.translator.translate(error, {
                'tool_name': context.tool_name,
                'command': context.command,
                'session_id': context.session_id
            })
            # 记录错误历史
            self.error_history.append(translated)
            # 限制历史记录大小
            if len(self.error_history) > 1000:
                self.error_history = self.error_history[-500:]
            # 将翻译后的错误注入上下文
            context.extra['translated_error'] = translated
            logger.info(f"Error translated: {translated.error_id} - {translated.user_message}")
    def get_error_history(self, limit: int = 100) -> List[TranslatedError]:
        """获取错误历史"""
        return self.error_history[-limit:]
    def get_error_statistics(self) -> Dict[str, Any]:
        """获取错误统计"""
        stats: Dict[str, Any] = {
            'total_errors': len(self.error_history),
            'by_category': {},
            'by_severity': {},
            'by_error_id': {},
            'auto_fixable_count': 0
        }
        for err in self.error_history:
            stats['by_category'][err.category] = stats['by_category'].get(err.category, 0) + 1
            stats['by_severity'][err.severity] = stats['by_severity'].get(err.severity, 0) + 1
            if err.error_id:
                stats['by_error_id'][err.error_id] = stats['by_error_id'].get(err.error_id, 0) + 1
            if err.auto_fixable:
                stats['auto_fixable_count'] += 1
        return stats
    def format_error_for_cli(self, translated: TranslatedError) -> str:
        """格式化错误信息用于CLI输出"""
        severity_colors = {
            'ERROR': 'bold red',
            'WARNING': 'bold yellow',
            'INFO': 'bold blue'
        }
        color = severity_colors.get(translated.severity, 'bold white')
        lines = []
        lines.append(f"[{color}]❌ {translated.user_message}[/{color}]")
        if translated.error_id:
            lines.append(f"   错误代码: {translated.error_id}")
        lines.append(f"   严重程度: {translated.severity}")
        lines.append(f"   预计修复时间: ~{translated.fix_time_estimate}分钟")
        if translated.solutions:
            lines.append("")
            lines.append("[bold cyan]🔧 解决方案:[/bold cyan]")
            for i, sol in enumerate(translated.solutions, 1):
                lines.append(f"   {i}. {sol}")
        if translated.suggestion:
            lines.append("")
            lines.append(f"[bold green]{translated.suggestion}[/bold green]")
        if translated.auto_fixable:
            lines.append("")
            lines.append("[bold green]✅ 此问题支持一键自动修复[/bold green]")
        return "\n".join(lines)
# 导出单例实例
error_kb = ErrorKnowledgeBase()
error_translator = ErrorTranslator()
__all__ = [
    'ErrorKnowledgeBase',
    'ErrorTranslator',
    'ErrorTranslationMiddleware',
    'TranslatedError',
    'ErrorKnowledge',
    'error_kb',
    'error_translator'
]
