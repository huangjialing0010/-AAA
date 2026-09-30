"""无重依赖的 Qlib 风格线性基线模型。"""

from __future__ import annotations

import math
from collections.abc import Iterable


FEATURES = ("return_5", "return_20", "volatility_20", "amount_median_5", "volume_median_5",
            "KMID", "KLEN", "KMID2", "KUP", "KUP2", "KLOW", "KLOW2", "KSFT", "KSFT2",
            "ROC20", "MA20", "STD20", "VMA20", "VSTD20", "MAX20", "MIN20", "RSV20")


def feature_definitions() -> tuple[dict[str, str], ...]:
    """返回当前适配子集的固定公式与数据约束。"""
    definitions = [
        {"name": "return_5", "formula": "close_qfq(t)/close_qfq(t-5)-1", "unit": "ratio", "missing": "any nonpositive close rejects sample"},
        {"name": "return_20", "formula": "close_qfq(t)/close_qfq(t-20)-1", "unit": "ratio", "missing": "any nonpositive close rejects sample"},
        {"name": "volatility_20", "formula": "stdev(close_qfq daily returns over t-19..t)*sqrt(252)", "unit": "annualized ratio", "missing": "any nonpositive close rejects sample"},
        {"name": "amount_median_5", "formula": "median(amount(t-4..t))", "unit": "source amount", "missing": "missing or negative amount rejects sample"},
        {"name": "volume_median_5", "formula": "median(volume(t-4..t))", "unit": "source volume", "missing": "missing or negative volume rejects sample"},
    ]
    definitions.extend({"name": name, "formula": "Alpha158 official formula mapped to local OHLC/close/volume", "unit": "ratio", "missing": "invalid source window rejects sample"} for name in FEATURES[5:])
    return tuple(definitions)


class RidgeRankModel:
    """训练集标准化 + 岭回归；用于规则适配验证，不等同于 LightGBM。"""

    def __init__(self, *, alpha: float = 1.0) -> None:
        if alpha <= 0 or not math.isfinite(alpha):
            raise ValueError("alpha 必须是有限正数")
        self.alpha = alpha
        self.means: list[float] | None = None
        self.scales: list[float] | None = None
        self.weights: list[float] | None = None
        self.intercept: float | None = None

    @staticmethod
    def _matrix(rows: Iterable[dict[str, str]]) -> tuple[list[list[float]], list[float]]:
        matrix: list[list[float]] = []
        labels: list[float] = []
        for row in rows:
            try:
                values = [float(row[name]) for name in FEATURES]
                label = float(row["label_forward_return"])
            except (KeyError, TypeError, ValueError):
                continue
            if all(math.isfinite(value) for value in values) and math.isfinite(label):
                matrix.append(values)
                labels.append(label)
        return matrix, labels

    def fit(self, rows: Iterable[dict[str, str]]) -> "RidgeRankModel":
        import numpy as np

        matrix, labels = self._matrix(rows)
        if len(matrix) < len(FEATURES) + 2:
            raise ValueError("有效训练样本不足")
        x = np.asarray(matrix, dtype=float)
        y = np.asarray(labels, dtype=float)
        means = x.mean(axis=0)
        scales = x.std(axis=0)
        scales[scales == 0] = 1.0
        z = (x - means) / scales
        design = np.column_stack([np.ones(len(z)), z])
        penalty = np.eye(design.shape[1]) * self.alpha
        penalty[0, 0] = 0.0
        beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        self.means = means.tolist()
        self.scales = scales.tolist()
        self.intercept = float(beta[0])
        self.weights = beta[1:].tolist()
        return self

    def predict(self, rows: Iterable[dict[str, str]]) -> list[float]:
        if self.means is None or self.scales is None or self.weights is None or self.intercept is None:
            raise RuntimeError("模型尚未训练")
        output: list[float] = []
        for row in rows:
            values = [float(row[name]) for name in FEATURES]
            score = self.intercept + sum(weight * (value - mean) / scale for value, mean, scale, weight in zip(values, self.means, self.scales, self.weights))
            output.append(score)
        return output
