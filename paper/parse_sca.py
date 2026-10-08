"""Parse OMNeT++ .sca scalar result files and compute paper metrics.

Usage:
    python parse_sca.py <file1.sca> [file2.sca ...]
    python parse_sca.py --compare <a.sca> <b.sca>

Metrics computed per file:
    - generated/received BSMs (beacon safety messages) summed over all modules
    - packet delivery ratio (PDR): total ReceivedBroadcasts / total SentPackets
      and BSM-level receivedBSMs / (generatedBSMs * (n_receivers)) variants
    - total collisions, RXTXLostPackets, mean channelBusy:timeavg
--compare prints a field-by-field diff of the two aggregate dicts.
"""

from pathlib import Path as _Path

HERE = _Path(__file__).resolve().parent   # 仓库中的 paper/ 目录
REPO = HERE.parent                        # 仓库根目录
import sys
import re
from collections import defaultdict


def parse(path):
    scalars = []  # (module, name, value)
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("scalar "):
                # scalar <module> <name> <value>
                m = re.match(r'scalar\s+(\S+)\s+("[^"]+"|\S+)\s+(\S+)', line)
                if m:
                    mod, name, val = m.groups()
                    try:
                        scalars.append((mod, name.strip('"'), float(val)))
                    except ValueError:
                        pass
    return scalars


def aggregate(scalars):
    import math
    agg = defaultdict(float)
    counts = defaultdict(int)
    for mod, name, val in scalars:
        if not math.isfinite(val):
            continue  # 零活动节点的 timeavg 会是 nan，跳过
        agg[name] += val
        counts[name] += 1
    n_nodes = counts.get("generatedBSMs", 0)
    res = {
        "modules_with_generatedBSMs": n_nodes,
        "generatedBSMs_total": agg.get("generatedBSMs", 0.0),
        "receivedBSMs_total": agg.get("receivedBSMs", 0.0),
        "SentPackets_total": agg.get("SentPackets", 0.0),
        "ReceivedBroadcasts_total": agg.get("ReceivedBroadcasts", 0.0),
        "RXTXLostPackets_total": agg.get("RXTXLostPackets", 0.0),
        "SNIRLostPackets_total": agg.get("SNIRLostPackets", 0.0),
        "TotalLostPackets_total": agg.get("TotalLostPackets", 0.0),
        "collisions_total": agg.get("collisions:count", 0.0),
        "channelBusy_timeavg_mean": (
            agg.get("channelBusy:timeavg", 0.0) / counts["channelBusy:timeavg"]
            if counts.get("channelBusy:timeavg") else 0.0
        ),
        "busyTime_total": agg.get("busyTime", 0.0),
        "totalTime_sum": agg.get("totalTime", 0.0),
    }
    sent = res["SentPackets_total"]
    res["PDR_broadcast"] = (
        res["ReceivedBroadcasts_total"] / (res["ReceivedBroadcasts_total"] + res["RXTXLostPackets_total"])
        if (res["ReceivedBroadcasts_total"] + res["RXTXLostPackets_total"]) > 0 else 0.0
    )
    # 帧接收成功率：在灵敏度以上到达接收机的帧中，被成功解码的比例。
    # 丢失既包括收发冲突（RXTXLost），也包括干扰/碰撞导致的信噪比不足（SNIRLost）；
    # 上面的 PDR_broadcast 只扣除了前者，会高估接收成功率，保留仅为与旧结果对照。
    lost = res["TotalLostPackets_total"]
    rx = res["ReceivedBroadcasts_total"]
    res["PDR_total"] = rx / (rx + lost) if (rx + lost) > 0 else 0.0
    res["recv_per_sent"] = res["ReceivedBroadcasts_total"] / sent if sent else 0.0
    return res


def main():
    args = sys.argv[1:]
    compare = False
    if args and args[0] == "--compare":
        compare = True
        args = args[1:]
    results = {}
    for p in args:
        results[p] = aggregate(parse(p))
    if compare and len(results) == 2:
        (pa, a), (pb, b) = results.items()
        print(f"{'metric':35s} {'A':>18s} {'B':>18s} {'diff%':>8s}")
        print(f"A = {pa}\nB = {pb}")
        for k in a:
            va, vb = a[k], b[k]
            d = 0.0 if va == vb else (abs(va - vb) / va * 100 if va else float("inf"))
            print(f"{k:35s} {va:18.6f} {vb:18.6f} {d:7.3f}%")
    else:
        for p, r in results.items():
            print(f"== {p}")
            for k, v in r.items():
                print(f"  {k:35s} {v:.6f}")


if __name__ == "__main__":
    main()
