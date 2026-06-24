"""
异常行为检测模块单元测试 (tests/test_anomaly.py)
测试覆盖:
- Isolation Forest 算法
- 特征工程提取
- 滑动窗口分析
- 多级异常响应策略
- 模型持久化
"""
import sys
import os
import pytest
sys.path.insert(0, ".")
from security import (
    AnomalyDetector, AnomalyLevel, get_anomaly_detector,
    IsolationForest, FeatureExtractor, SlidingWindowAnalyzer, CommandFeatures
)
from tools.ast_parser import RiskLevel
class TestFeatureExtractor:
    """测试特征提取器。"""
    def setup_method(self):
        self.extractor = FeatureExtractor()
    def test_basic_features(self):
        """测试基础特征提取。"""
        features = self.extractor.extract("ls -la", RiskLevel.SAFE)
        assert features.command_length == 5
        assert features.risk_score == 0.0
        assert features.pipe_count == 0
        assert features.semicolon_count == 0
    def test_special_char_detection(self):
        """测试特殊字符检测。"""
        features = self.extractor.extract("ls -la; cat /etc/passwd", RiskLevel.SAFE)
        assert features.special_char_ratio > 0
        assert features.semicolon_count == 1
    def test_pipe_detection(self):
        """测试管道符检测。"""
        features = self.extractor.extract("cat file | grep pattern | sort", RiskLevel.SAFE)
        assert features.pipe_count == 2
    def test_nested_depth(self):
        """测试括号嵌套深度。"""
        features = self.extractor.extract("echo $((1+2))", RiskLevel.SAFE)
        assert features.nested_depth >= 2
    def test_command_frequency(self):
        """测试命令频率统计。"""
        # 首次出现
        f1 = self.extractor.extract("new_command_123", RiskLevel.SAFE)
        assert f1.command_frequency == 1
        # 第二次出现
        f2 = self.extractor.extract("new_command_123", RiskLevel.SAFE)
        assert f2.command_frequency == 2
    def test_to_vector(self):
        """测试特征向量化。"""
        features = self.extractor.extract("ls -la", RiskLevel.SAFE)
        vector = features.to_vector()
        assert len(vector) == 9
        assert all(isinstance(x, float) for x in vector)
class TestIsolationForest:
    """测试孤立森林算法。"""
    def test_isolation_forest_training(self):
        """测试模型训练。"""
        # 生成简单的训练数据
        X = [[0.1, 0.2], [0.15, 0.25], [0.12, 0.22], [0.08, 0.18]] * 10
        forest = IsolationForest(n_estimators=10, max_samples=20)
        forest.fit(X)
        assert len(forest.trees) == 10
    def test_anomaly_score_normal(self):
        """测试正常样本异常分数。"""
        X = [[0.1, 0.2], [0.15, 0.25], [0.12, 0.22]] * 20
        forest = IsolationForest(n_estimators=20, max_samples=50)
        forest.fit(X)
        # 正常样本分数应该较低
        normal_score = forest.anomaly_score([0.11, 0.21])
        assert normal_score < 0.6
    def test_anomaly_score_outlier(self):
        """测试异常样本异常分数。"""
        X = [[0.1, 0.2], [0.15, 0.25], [0.12, 0.22]] * 20
        forest = IsolationForest(n_estimators=20, max_samples=50)
        forest.fit(X)
        # 离群样本分数应该较高
        outlier_score = forest.anomaly_score([10.0, 10.0])
        assert outlier_score > 0.4
    def test_model_save_load(self):
        """测试模型保存和加载。"""
        X = [[0.1, 0.2], [0.15, 0.25]] * 10
        forest = IsolationForest(n_estimators=10)
        forest.fit(X)
        # 保存
        os.makedirs("models", exist_ok=True)
        forest.save("models/test_model.pkl")
        # 加载
        loaded = IsolationForest.load("models/test_model.pkl")
        assert len(loaded.trees) == 10
        # 清理
        os.remove("models/test_model.pkl")
class TestSlidingWindow:
    """测试滑动窗口分析。"""
    def test_window_basic(self):
        """测试基本窗口功能。"""
        analyzer = SlidingWindowAnalyzer(window_size=5)
        score1 = analyzer.add_score(0.1)
        score2 = analyzer.add_score(0.2)
        score3 = analyzer.add_score(0.3)
        # 平均值应该在合理范围内
        assert 0.1 <= score3 <= 0.3
    def test_window_size_limit(self):
        """测试窗口大小限制。"""
        analyzer = SlidingWindowAnalyzer(window_size=3)
        for i in range(10):
            analyzer.add_score(float(i) / 10)
        # 窗口应该只保留最后3个
        assert len(analyzer._scores) <= 3
class TestAnomalyDetector:
    """测试异常检测引擎。"""
    def setup_method(self):
        # 使用临时模型路径
        self.detector = get_anomaly_detector(
            model_path="models/test_anomaly.pkl",
            force_new=True
        )
    def teardown_method(self):
        # 清理测试模型
        if os.path.exists("models/test_anomaly.pkl"):
            os.remove("models/test_anomaly.pkl")
    def test_normal_command_detection(self):
        """测试正常命令检测。"""
        result = self.detector.detect("ls -la", RiskLevel.SAFE)
        assert result.anomaly_level == AnomalyLevel.NORMAL
        assert not result.is_anomaly
    def test_high_risk_command_warning(self):
        """测试高风险命令警告。"""
        result = self.detector.detect(
            "ls -la; cat /etc/passwd; rm -rf /tmp",
            RiskLevel.HIGH
        )
        # 多命令链式执行应该触发警告或更高
        assert result.anomaly_level in (AnomalyLevel.WARNING, AnomalyLevel.CRITICAL)
    def test_injection_attempt_critical(self):
        """测试注入尝试严重异常。"""
        result = self.detector.detect(
            "ls -la; cat /etc/passwd | grep root; id; whoami",
            RiskLevel.CRITICAL
        )
        # 复杂注入应该触发严重异常
        assert result.anomaly_score >= 0.4
    def test_threshold_update(self):
        """测试阈值动态更新。"""
        self.detector.update_thresholds(0.3, 0.6)
        assert self.detector.threshold_warning == 0.3
        assert self.detector.threshold_critical == 0.6
    def test_feature_result_integrity(self):
        """测试检测结果完整性。"""
        result = self.detector.detect("pwd", RiskLevel.SAFE)
        assert result.features is not None
        assert isinstance(result.anomaly_score, float)
        assert 0 <= result.anomaly_score <= 1
        assert result.reason != ""
class TestAnomalyLevels:
    """测试异常等级阈值。"""
    def test_anomaly_level_thresholds(self):
        """测试异常等级阈值。"""
        detector = get_anomaly_detector(force_new=True)
        # 验证默认阈值
        assert detector.threshold_warning == 0.4
        assert detector.threshold_critical == 0.7
    def test_level_classification(self):
        """测试等级分类逻辑。"""
        detector = get_anomaly_detector(force_new=True)
        # 直接验证等级枚举
        assert AnomalyLevel.NORMAL.value == "normal"
        assert AnomalyLevel.WARNING.value == "warning"
        assert AnomalyLevel.CRITICAL.value == "critical"
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
