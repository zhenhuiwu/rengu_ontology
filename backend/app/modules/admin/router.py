from fastapi import APIRouter

# 占位：负责人 PDE工程师。挂载在 /api/admin/v1 下，路由路径用资源名复数，例如 "/objects"。
router = APIRouter(tags=["admin"])
