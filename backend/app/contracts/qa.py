"""QA对外查询：静态指标回滚前检查已保存的指标软引用。"""

from app.modules.qa.service import has_metric_references

__all__ = ["has_metric_references"]
