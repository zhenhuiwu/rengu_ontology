"""公共层。

⚠️ 归属：本目录的 auth / audit / pagination / context 是 **PDE工程师** 负责的公共部分。
骨架里只放了「接口形状 + 最小桩实现」，标了 `EXTENSION POINT (PDE)` 的地方由 PDE 替换成真实实现；
各业务模块只依赖这里导出的名字（函数签名保持不变），这样 PDE 换实现时业务模块不用改。
repository.py 是骨架（本体小白）负责的通用数据访问基类。
"""
