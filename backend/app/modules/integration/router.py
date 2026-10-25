from fastapi import APIRouter

# 占位：负责人 Palantir专家。挂载在 /api/data/v1 下，
# 路由路径用资源名复数，例如 "/ext-systems"。
router = APIRouter(tags=["integration"])
