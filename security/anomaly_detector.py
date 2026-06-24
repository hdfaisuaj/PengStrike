"""
异常行为检测模块 (security/anomaly_detector.py) - Phase 3 安全架构重构
职责:
- Isolation Forest 算法实现（纯Python无依赖）
- 特征工程：命令长度、特殊字符占比、风险等级加权、频率分析
- 滑动窗口序列分析
- 多级异常响应策略（<0.4日志 / 0.4-0.7二次确认 / >0.7拦截）
- 模型持久化与热加载
设计原则:
- 无外部依赖（不依赖scikit-learn）
- 轻量级，低性能开销
- 可解释的异常分数
- 在线学习支持
"""
from __future__ import annotations
import math
import pickle
import random
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
from tools.ast_parser import RiskLevel
from utils.logger import get_logger
logger = get_logger(__name__)
# ========================================================================
# 异常响应等级
# ========================================================================
class AnomalyLevel(Enum):
    """异常响应等级。"""
    NORMAL = "normal"           # < 0.4: 正常，仅日志
    WARNING = "warning"         # 0.4-0.7: 警告，二次确认
    CRITICAL = "critical"       # > 0.7: 严重，直接拦截
# ========================================================================
# 特征向量
# ========================================================================
@dataclass
class CommandFeatures:
    """命令特征向量。"""
    command_length: int = 0              # 命令长度
    special_char_ratio: float = 0.0      # 特殊字符占比
    risk_score: float = 0.0              # 风险等级加权分
    pipe_count: int = 0                   # 管道符数量
    semicolon_count: int = 0              # 分号数量
    dollar_count: int = 0                 # $符号数量
    backtick_count: int = 0               # 反引号数量
    nested_depth: int = 0                 # 括号嵌套深度
    command_frequency: float = 1.0        # 命令出现频率（越低越异常）
    timestamp: float = field(default_factory=time.time)
    def to_vector(self) -> List[float]:
        """转换为数值向量。"""
        return [
            float(self.command_length) / 100.0,      # 归一化
            self.special_char_ratio,
            self.risk_score,
            float(self.pipe_count) / 5.0,
            float(self.semicolon_count) / 5.0,
            float(self.dollar_count) / 5.0,
            float(self.backtick_count) / 3.0,
            float(self.nested_depth) / 10.0,
            1.0 / max(1.0, self.command_frequency),
        ]
# ========================================================================
# 检测结果
# ========================================================================
@dataclass
class AnomalyResult:
    """异常检测结果。"""
    is_anomaly: bool
    anomaly_score: float
    anomaly_level: AnomalyLevel
    features: CommandFeatures
    reason: str = ""
    window_score: float = 0.0
    @classmethod
    def normal(cls, features: CommandFeatures, score: float) -> "AnomalyResult":
        return cls(
            is_anomaly=False,
            anomaly_score=score,
            anomaly_level=AnomalyLevel.NORMAL,
            features=features,
            reason=f"正常行为 (分数: {score:.3f})",
        )
    @classmethod
    def warning(cls, features: CommandFeatures, score: float, reason: str) -> "AnomalyResult":
        return cls(
            is_anomaly=True,
            anomaly_score=score,
            anomaly_level=AnomalyLevel.WARNING,
            features=features,
            reason=f"异常行为警告: {reason} (分数: {score:.3f})",
        )
    @classmethod
    def critical(cls, features: CommandFeatures, score: float, reason: str) -> "AnomalyResult":
        return cls(
            is_anomaly=True,
            anomaly_score=score,
            anomaly_level=AnomalyLevel.CRITICAL,
            features=features,
            reason=f"严重异常行为: {reason} (分数: {score:.3f})",
        )
# ========================================================================
# Isolation Forest 实现（纯Python）
# ========================================================================
class IsolationTree:
    """孤立树。"""
    def __init__(self, max_depth: int = 10):
        self.max_depth = max_depth
        self.split_attr = -1
        self.split_value = 0.0
        self.left: Optional[IsolationTree] = None
        self.right: Optional[IsolationTree] = None
        self.size = 0
    def build(self, data: List[List[float]], depth: int = 0) -> None:
        """递归构建孤立树。"""
        self.size = len(data)
        if depth >= self.max_depth or self.size <= 1:
            return
        # 随机选择属性和分割值
        n_features = len(data[0]) if data else 0
        self.split_attr = random.randint(0, n_features - 1)
        values = [x[self.split_attr] for x in data]
        min_val, max_val = min(values), max(values)
        if min_val == max_val:
            return
        self.split_value = random.uniform(min_val, max_val)
        # 分割数据
        left_data = [x for x in data if x[self.split_attr] < self.split_value]
        right_data = [x for x in data if x[self.split_attr] >= self.split_value]
        # 递归构建
        if left_data:
            self.left = IsolationTree(self.max_depth)
            self.left.build(left_data, depth + 1)
        if right_data:
            self.right = IsolationTree(self.max_depth)
            self.right.build(right_data, depth + 1)
    def path_length(self, sample: List[float], depth: int = 0) -> float:
        """计算样本路径长度。"""
        if self.left is None and self.right is None:
            return depth + self._c(self.size)
        if sample[self.split_attr] < self.split_value:
            if self.left:
                return self.left.path_length(sample, depth + 1)
        else:
            if self.right:
                return self.right.path_length(sample, depth + 1)
        return depth
    @staticmethod
    def _c(n: int) -> float:
        """平均路径长度调整因子。"""
        if n <= 1:
            return 0.0
        return 2.0 * (math.log(n - 1) + 0.5772156649) - 2.0 * (n - 1) / n
class IsolationForest:
    """孤立森林（纯Python实现）。"""
    def __init__(self, n_estimators: int = 50, max_samples: int = 256, max_depth: int = 10):
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.max_depth = max_depth
        self.trees: List[IsolationTree] = []
        self._trained = False
    def fit(self, X: List[List[float]]) -> None:
        """训练模型。"""
        n_samples = min(len(X), self.max_samples)
        for _ in range(self.n_estimators):
            # 子采样
            indices = random.sample(range(len(X)), n_samples)
            subset = [X[i] for i in indices]
            tree = IsolationTree(self.max_depth)
            tree.build(subset)
            self.trees.append(tree)
        self._trained = True
        logger.info("Isolation Forest 训练完成，%d 棵树", self.n_estimators)
    def anomaly_score(self, sample: List[float]) -> float:
        """计算异常分数 (0-1，越高越异常)。"""
        if not self._trained or not self.trees:
            return 0.0
        # 计算平均路径长度
        avg_path = sum(tree.path_length(sample) for tree in self.trees) / len(self.trees)
        # 归一化到 0-1
        c = IsolationTree._c(self.max_samples)
        score = math.pow(2, -avg_path / c)
        return min(1.0, max(0.0, score))
    def save(self, path: str) -> None:
        """保存模型。"""
        with open(path, "wb") as f:
            pickle.dump(self, f)
        logger.info("模型已保存到: %s", path)
    @classmethod
    def load(cls, path: str) -> "IsolationForest":
        """加载模型。"""
        with open(path, "rb") as f:
            model = pickle.load(f)
        logger.info("模型已从 %s 加载", path)
        return model
# ========================================================================
# 特征提取器
# ========================================================================
class FeatureExtractor:
    """命令特征提取器。"""
    def __init__(self):
        self._command_history: Dict[str, int] = {}  # 命令频率统计
        self._special_chars = set(";|`$(){}[]<>\\&!@#%^*")
    def extract(self, command: str, risk_level: RiskLevel = RiskLevel.SAFE) -> CommandFeatures:
        """提取命令特征。"""
        cmd = command.strip()
        # 基础特征
        length = len(cmd)
        special_count = sum(1 for c in cmd if c in self._special_chars)
        special_ratio = special_count / max(1, length)
        # 风险等级映射为分数
        risk_scores = {
            RiskLevel.SAFE: 0.0,
            RiskLevel.LOW: 0.2,
            RiskLevel.MEDIUM: 0.5,
            RiskLevel.HIGH: 0.8,
            RiskLevel.CRITICAL: 1.0,
        }
        risk_score = risk_scores.get(risk_level, 0.5)
        # 特殊符号计数
        pipe_count = cmd.count("|")
        semicolon_count = cmd.count(";")
        dollar_count = cmd.count("$")
        backtick_count = cmd.count("`")
        # 括号嵌套深度
        nested_depth = self._calc_nested_depth(cmd)
        # 频率特征（首次出现频率高）
        cmd_key = cmd.split()[0] if cmd.split() else cmd
        self._command_history[cmd_key] = self._command_history.get(cmd_key, 0) + 1
        frequency = self._command_history[cmd_key]
        return CommandFeatures(
            command_length=length,
            special_char_ratio=special_ratio,
            risk_score=risk_score,
            pipe_count=pipe_count,
            semicolon_count=semicolon_count,
            dollar_count=dollar_count,
            backtick_count=backtick_count,
            nested_depth=nested_depth,
            command_frequency=frequency,
        )
    def _calc_nested_depth(self, cmd: str) -> int:
        """计算括号嵌套深度。"""
        max_depth = 0
        current = 0
        for c in cmd:
            if c in "([{":
                current += 1
                max_depth = max(max_depth, current)
            elif c in ")]}":
                current = max(0, current - 1)
        return max_depth
# ========================================================================
# 滑动窗口分析器
# ========================================================================
class SlidingWindowAnalyzer:
    """滑动窗口序列分析器。"""
    def __init__(self, window_size: int = 20, time_window: int = 300):
        self.window_size = window_size
        self.time_window = time_window
        self._scores: deque = deque(maxlen=window_size)
        self._timestamps: deque = deque(maxlen=window_size)
    def add_score(self, score: float) -> float:
        """添加分数并返回窗口异常分数。"""
        now = time.time()
        self._scores.append(score)
        self._timestamps.append(now)
        # 清理过期数据
        cutoff = now - self.time_window
        while self._timestamps and self._timestamps[0] < cutoff:
            self._scores.popleft()
            self._timestamps.popleft()
        # 计算窗口分数：最近N个的平均值 + 趋势
        if not self._scores:
            return 0.0
        avg_score = sum(self._scores) / len(self._scores)
        # 趋势检测：最近5个的上升趋势
        if len(self._scores) >= 5:
            recent = list(self._scores)[-5:]
            trend = sum(recent[i] > recent[i-1] for i in range(1, 5)) / 4.0
            return avg_score * (1 + trend * 0.5)
        return avg_score
# ========================================================================
# 异常检测引擎
# ========================================================================
class AnomalyDetector:
    """异常行为检测引擎。"""
    def __init__(
        self,
        model_path: Optional[str] = None,
        auto_train: bool = True,
    ) -> None:
        self._model_path = Path(model_path) if model_path else Path("models/anomaly_model.pkl")
        self._extractor = FeatureExtractor()
        self._window_analyzer = SlidingWindowAnalyzer()
        self._model: Optional[IsolationForest] = None
        self._last_reload: float = 0
        self._reload_interval: float = 300.0  # 5分钟热加载
        # 阈值配置
        self.threshold_warning = 0.6
        self.threshold_critical = 0.8
        # 尝试加载或训练模型
        if self._model_path.exists():
            self._load_model()
        elif auto_train:
            self._train_default_model()
        logger.info("异常行为检测引擎初始化完成")
    def _train_default_model(self) -> None:
        """训练默认模型（使用合成正常数据）。"""
        logger.info("训练默认异常检测模型...")
        # 生成正常行为的训练数据
        normal_data = self._generate_normal_training_data()
        self._model = IsolationForest(n_estimators=30, max_samples=100)
        self._model.fit(normal_data)
        # 保存模型
        self._model_path.parent.mkdir(parents=True, exist_ok=True)
        self._model.save(str(self._model_path))
    def _generate_normal_training_data(self) -> List[List[float]]:
        """生成正常行为的训练数据。"""
        # 模拟正常命令的特征分布
        normal_commands = [
            "ls -la", "pwd", "echo hello", "cat file.txt",
            "head -20 log.txt", "tail -f log", "grep pattern file",
            "find . -name *.py", "which python", "id", "uname -a",
            "date", "ping -c 4 example.com", "nslookup example.com",
        ]
        data = []
        for cmd in normal_commands:
            features = self._extractor.extract(cmd, RiskLevel.SAFE)
            data.append(features.to_vector())
        # 添加一些变体
        for _ in range(50):
            base = random.choice(normal_commands)
            features = self._extractor.extract(base + " " * random.randint(0, 5), RiskLevel.SAFE)
            data.append(features.to_vector())
        return data
    def _load_model(self) -> None:
        """加载模型。"""
        try:
            self._model = IsolationForest.load(str(self._model_path))
        except Exception as e:
            logger.warning("加载异常检测模型失败，将重新训练: %s", e)
            self._train_default_model()
    def _check_reload(self) -> None:
        """检查模型热加载。"""
        now = time.time()
        if now - self._last_reload > self._reload_interval:
            if self._model_path.exists():
                mtime = self._model_path.stat().st_mtime
                if mtime > self._last_reload:
                    try:
                        self._load_model()
                        logger.info("异常检测模型热加载完成")
                    except Exception as e:
                        logger.warning("模型热加载失败: %s", e)
            self._last_reload = now
    def detect(
        self,
        command: str,
        risk_level: RiskLevel = RiskLevel.SAFE,
    ) -> AnomalyResult:
        """检测命令异常行为。
        参数:
            command: 待检测的命令
            risk_level: AST检测的风险等级
        返回:
            AnomalyResult 检测结果
        """
        self._check_reload()
        # 提取特征
        features = self._extractor.extract(command, risk_level)
        vector = features.to_vector()
        # 计算异常分数
        if self._model:
            score = self._model.anomaly_score(vector)
        else:
            score = features.risk_score * 0.5 + features.special_char_ratio * 0.5
        # 滑动窗口分析
        window_score = self._window_analyzer.add_score(score)
        final_score = max(score, window_score)
        # 根据阈值判断
        if final_score >= self.threshold_critical:
            return AnomalyResult.critical(
                features=features,
                score=final_score,
                reason=self._get_anomaly_reason(features),
            )
        if final_score >= self.threshold_warning:
            return AnomalyResult.warning(
                features=features,
                score=final_score,
                reason=self._get_anomaly_reason(features),
            )
        result = AnomalyResult.normal(features, final_score)
        result.window_score = window_score
        return result
    def _get_anomaly_reason(self, features: CommandFeatures) -> str:
        """生成异常原因描述。"""
        reasons = []
        if features.special_char_ratio > 0.3:
            reasons.append(f"特殊字符占比高 ({features.special_char_ratio:.1%})")
        if features.risk_score > 0.6:
            reasons.append("高风险操作")
        if features.pipe_count + features.semicolon_count > 2:
            reasons.append("多命令链式执行")
        if features.nested_depth > 3:
            reasons.append("深层嵌套结构")
        if features.command_frequency == 1:
            reasons.append("首次出现的命令")
        if not reasons:
            reasons.append("行为模式异常")
        return ", ".join(reasons)
    def update_thresholds(self, warning: float, critical: float) -> None:
        """更新检测阈值（热更新）。"""
        self.threshold_warning = warning
        self.threshold_critical = critical
        logger.info("异常检测阈值已更新: warning=%.2f, critical=%.2f", warning, critical)
# ========================================================================
# 单例工厂
# ========================================================================
_default_detector: Optional[AnomalyDetector] = None
def get_anomaly_detector(model_path: Optional[str] = None, force_new: bool = False) -> AnomalyDetector:
    global _default_detector
    if _default_detector is None or force_new:
        _default_detector = AnomalyDetector(model_path)
    return _default_detector
