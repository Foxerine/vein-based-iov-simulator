from utils.result_analysis import ANALYSIS_VERSION, summarize_sca


def test_reception_ratio_counts_all_lost_frames():
    """干扰导致的丢失（SNIRLost）与收发冲突丢失（RXTXLost）都应计入，TotalLostPackets 为两者之和。"""
    scalars = [
        ("net.node[0].nic.phy80211p", "ReceivedBroadcasts", 90.0),
        ("net.node[0].nic.phy80211p", "SNIRLostPackets", 6.0),
        ("net.node[0].nic.phy80211p", "RXTXLostPackets", 4.0),
        ("net.node[0].nic.phy80211p", "TotalLostPackets", 10.0),
    ]
    s = summarize_sca(scalars)
    assert s["lost_packets"] == 10
    assert s["pdr_pct"] == 90.0


def test_analysis_version_invalidates_v1_cache():
    assert ANALYSIS_VERSION >= 2
