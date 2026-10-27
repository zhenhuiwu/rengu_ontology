"""Source rules shared by dimension CR checks, publication and restoration."""


def dimension_source_error(kind, object_code, objects, *, attr="", column=""):
    if kind not in ("维度", "码表"):
        return "类型须为维度或码表"
    if not isinstance(object_code, str):
        return "来源对象须为有效对象编号"
    if not object_code:
        if kind == "维度":
            return "维度须选择来源对象"
        if attr or column:
            return "独立码表不能设置来源属性或源列"
    elif object_code not in objects:
        return "来源对象须属于当前本体或同单已保存的对象定义"
    return None


def query_dimension_names(dimensions, metrics):
    """Unbound code tables remain metadata, never SQL grouping/filter candidates."""
    def aliases(d):
        return [d.get("name"), *(level.get("name") for level in d.get("levels") or [])]

    bound = {name for d in dimensions if d.get("object") for name in aliases(d) if name}
    unbound = {name for d in dimensions if not d.get("object") for name in aliases(d) if name} - bound
    names = [level.get("name") for d in dimensions if d.get("object") and d.get("status") == "已发布"
             for level in d.get("levels") or []]
    names += [name for metric in metrics for name in metric.get("dimNames") or []]
    return list(dict.fromkeys(name for name in names if isinstance(name, str) and name and name not in unbound))
