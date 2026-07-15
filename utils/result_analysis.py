# 仿真结果分析：解析 OMNeT++ 的 .sca / .vec 结果文件，产出汇总指标与时序数据
import os
import re
from collections import defaultdict


def parse_sca(path: str) -> list[tuple[str, str, float]]:
    """解析 .sca 标量文件，返回 (module, name, value) 列表"""
    scalars = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("scalar "):
                m = re.match(r'scalar\s+(\S+)\s+("[^"]+"|\S+)\s+(\S+)', line)
                if m:
                    mod, name, val = m.groups()
                    try:
                        scalars.append((mod, name.strip('"'), float(val)))
                    except ValueError:
                        pass
    return scalars


def summarize_sca(scalars: list[tuple[str, str, float]]) -> dict:
    """聚合出论文关注的核心指标"""
    agg = defaultdict(float)
    counts = defaultdict(int)
    for _, name, val in scalars:
        agg[name] += val
        counts[name] += 1
    received = agg.get("ReceivedBroadcasts", 0.0)
    lost = agg.get("RXTXLostPackets", 0.0)
    busy_mean = (agg.get("channelBusy:timeavg", 0.0) / counts["channelBusy:timeavg"]
                 if counts.get("channelBusy:timeavg") else 0.0)
    return {
        "sent_packets": int(agg.get("SentPackets", 0)),
        "received_broadcasts": int(received),
        "lost_packets": int(lost),
        "collisions": int(agg.get("collisions:count", 0)),
        "pdr_pct": round(received / (received + lost) * 100, 2) if received + lost > 0 else None,
        "channel_busy_pct": round(busy_mean * 100, 4),
        "node_count": counts.get("channelBusy:timeavg", 0),
    }


def per_module_table(scalars: list[tuple[str, str, float]], top_n: int = 15) -> list[dict]:
    """逐模块统计（按接收广播数降序），用于前端表格"""
    mods: dict[str, dict] = defaultdict(dict)
    for mod, name, val in scalars:
        # 归一到节点级模块名，如 RSUExampleScenario.node[3]
        m = re.match(r"([^.]+\.(?:node|rsu)\[\d+\])", mod)
        if not m:
            continue
        node = m.group(1).split(".", 1)[1]
        d = mods[node]
        if name == "SentPackets":
            d["sent"] = d.get("sent", 0) + int(val)
        elif name == "ReceivedBroadcasts":
            d["received"] = d.get("received", 0) + int(val)
        elif name == "busyTime":
            d["busy_time_s"] = round(d.get("busy_time_s", 0.0) + val, 3)
        elif name == "TotalLostPackets":
            d["lost"] = d.get("lost", 0) + int(val)
    rows = [{"node": k, "sent": v.get("sent", 0), "received": v.get("received", 0),
             "lost": v.get("lost", 0), "busy_time_s": v.get("busy_time_s", 0.0)}
            for k, v in mods.items()]
    rows.sort(key=lambda r: r["received"], reverse=True)
    return rows[:top_n]


def timeseries_from_vec(path: str, bucket: float = 2.0) -> dict:
    """从 .vec 提取时序：平均车速、在网车辆数、CO2 排放率（按 bucket 秒分桶）"""
    speed_ids, co2_ids = set(), set()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("vector "):
                parts = line.split()
                if len(parts) >= 4:
                    vid, name = parts[1], parts[3]
                    if name == "speed":
                        speed_ids.add(vid)
                    elif name == "co2emission":
                        co2_ids.add(vid)
            elif line[:1].isdigit():
                break  # 数据区开始，声明已收集完（OMNeT++ 声明穿插时继续走下面的全文件扫描）

    speed_sum: dict[int, float] = defaultdict(float)
    speed_cnt: dict[int, int] = defaultdict(int)
    speed_vehicles: dict[int, set] = defaultdict(set)
    co2_sum: dict[int, float] = defaultdict(float)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line[:1]
            if not c.isdigit():
                if c == "v":  # 声明穿插在数据区中，继续补充
                    parts = line.split()
                    if len(parts) >= 4 and parts[0] == "vector":
                        if parts[3] == "speed":
                            speed_ids.add(parts[1])
                        elif parts[3] == "co2emission":
                            co2_ids.add(parts[1])
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            vid = parts[0]
            try:
                t = float(parts[2]); val = float(parts[3])
            except ValueError:
                continue
            b = int(t // bucket)
            if vid in speed_ids:
                speed_sum[b] += val; speed_cnt[b] += 1
                speed_vehicles[b].add(vid)
            elif vid in co2_ids:
                co2_sum[b] += val

    buckets = sorted(set(speed_cnt) | set(co2_sum))
    return {
        "bucket_s": bucket,
        "time": [round(b * bucket, 1) for b in buckets],
        "avg_speed": [round(speed_sum[b] / speed_cnt[b], 2) if speed_cnt.get(b) else None
                      for b in buckets],
        "vehicle_count": [len(speed_vehicles.get(b, ())) for b in buckets],
        "co2_g": [round(co2_sum.get(b, 0.0), 2) for b in buckets],
    }


def analyze_run_dir(run_dir: str) -> dict:
    """分析一个运行结果目录，返回完整分析结果"""
    sca = vec = None
    for fn in os.listdir(run_dir):
        if fn.endswith(".sca"):
            sca = os.path.join(run_dir, fn)
        elif fn.endswith(".vec"):
            vec = os.path.join(run_dir, fn)
    if not sca:
        raise FileNotFoundError("结果目录中没有 .sca 标量文件（仿真可能未成功完成）")
    scalars = parse_sca(sca)
    result = {
        "summary": summarize_sca(scalars),
        "modules": per_module_table(scalars),
        "timeseries": timeseries_from_vec(vec) if vec else None,
    }
    return result
