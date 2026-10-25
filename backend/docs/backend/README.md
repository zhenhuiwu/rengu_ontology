# docs/backend/ —— 后台团队文档的位置

**约定：后台的团队文档统一放在仓库顶层 `docs/backend/`**（即 `backend/../docs/backend/`），和前端已有的
`static-api.md`、`qa-api.md`、`action-event-api.md` 放在一起；`backend/` 目录里只放代码和一份运行用的 README。

这个骨架目前是一个独立的 git 仓库（`/workspace/ontology-xiaobai/backend`），没有上层目录可放，所以先把文档放在
`backend/docs/backend/`。**合并进 rengu_ontology 时（由 PDE工程师操作）把本目录下的文件移到顶层 `docs/backend/`**：

| 文件 | 合并后位置 |
|---|---|
| `CONVENTIONS.md` | `docs/backend/CONVENTIONS.md` |
| 本文件 | 删除（约定已写进 CONVENTIONS.md §0） |

各模块负责人的接口文档（如 `static-api.md`）继续由各自维护，写法遵守 CONVENTIONS.md。
