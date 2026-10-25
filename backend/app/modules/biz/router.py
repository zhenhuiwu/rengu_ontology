from fastapi import APIRouter

# 占位：负责人 待定（PDE 指定）。挂载在 /api/biz/v1 [推断] 下，
# 路由路径用资源名复数，例如 "/stores"。
router = APIRouter(tags=["biz"])
