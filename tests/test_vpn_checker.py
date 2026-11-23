"""
Offline tests for vpn_checker.py.

All network calls are replaced with a fake "network" whose state can be flipped
between "VPN off" (real ISP) and "VPN on" (VPN server), so the tests run
without internet access and without a real VPN.
"""
import argparse
import json

import pytest

import vpn_checker as v

REAL = {"ip": "103.1.1.1", "country": "BD", "city": "Dhaka", "org": "AS1111 Local ISP"}
VPN = {"ip": "185.2.2.2", "country": "DE", "city": "Frankfurt", "org": "AS9009 M247 Europe"}


def ns(**kwargs):
    return argparse.Namespace(**kwargs)


@pytest.fixture
def net(monkeypatch, tmp_path):
    """Fake network. Flip net['vpn'] to switch between VPN off/on."""
    monkeypatch.chdir(tmp_path)  # baseline/report files go to a temp dir
    state = {"vpn": False, "ipv6": None, "dns_org": "AS9009 M247 Europe"}

    cur = lambda: VPN if state["vpn"] else REAL
    monkeypatch.setattr(v, "get_ip_info", lambda: cur())
    monkeypatch.setattr(v, "get_ip_simple", lambda timeout=3: cur()["ip"])
    monkeypatch.setattr(v, "get_ipv6", lambda: state["ipv6"])
    monkeypatch.setattr(v, "get_local_dns",
                        lambda: ["10.8.0.1"] if state["vpn"] else ["192.168.0.1"])
    monkeypatch.setattr(v, "dns_leak_test", lambda: [
        {"ip": "185.2.2.9", "country_name": "Germany", "asn": state["dns_org"]}])
    monkeypatch.setattr(v, "measure_speed", lambda size_mb=10: 40.0 if state["vpn"] else 100.0)
    monkeypatch.setattr(v, "measure_latency", lambda samples=5: 80.0 if state["vpn"] else 20.0)
    return state


def check_args(**kw):
    base = dict(country="DE", out=None, html=None, speed=False, max_speed_loss=50)
    base.update(kw)
    return ns(**base)


def test_baseline_is_saved_with_speed(net, tmp_path):
    assert v.cmd_baseline(ns(speed=True)) == 0
    data = json.loads((tmp_path / "vpn_baseline.json").read_text())
    assert data["ip_info"]["ip"] == REAL["ip"]
    assert data["speed"]["mbps"] == 100.0


def test_check_fails_when_vpn_is_off(net):
    v.cmd_baseline(ns(speed=False))
    assert v.cmd_check(check_args()) == 1


def test_check_passes_when_vpn_is_on(net):
    v.cmd_baseline(ns(speed=False))
    net["vpn"] = True
    assert v.cmd_check(check_args()) == 0


def test_check_fails_on_wrong_country(net):
    v.cmd_baseline(ns(speed=False))
    net["vpn"] = True
    assert v.cmd_check(check_args(country="US")) == 1


def test_dns_leak_is_detected(net):
    v.cmd_baseline(ns(speed=False))
    net["vpn"] = True
    net["dns_org"] = "AS1111 Local ISP"  # resolver belongs to the real ISP
    assert v.cmd_check(check_args()) == 1


def test_ipv6_leak_is_detected(net):
    net["ipv6"] = "2001:db8::1"
    v.cmd_baseline(ns(speed=False))
    net["vpn"] = True  # IPv6 address is unchanged -> leak
    assert v.cmd_check(check_args()) == 1


def test_speed_threshold(net):
    v.cmd_baseline(ns(speed=True))
    net["vpn"] = True  # 100 -> 40 Mbps = 60% loss
    assert v.cmd_check(check_args(speed=True, max_speed_loss=50)) == 1
    assert v.cmd_check(check_args(speed=True, max_speed_loss=70)) == 0


def test_reports_are_written(net, tmp_path):
    v.cmd_baseline(ns(speed=False))
    net["vpn"] = True
    out, page = tmp_path / "r.json", tmp_path / "r.html"
    v.cmd_check(check_args(out=str(out), html=str(page)))
    assert json.loads(out.read_text())["current"]["ip"] == VPN["ip"]
    html = page.read_text()
    assert "PASSED" in html and "Frankfurt" in html


def test_wait_detects_connection(net):
    v.cmd_baseline(ns(speed=False))
    net["vpn"] = True
    assert v.cmd_wait(ns(timeout=3)) == 0


def test_wait_times_out_when_vpn_never_connects(net):
    v.cmd_baseline(ns(speed=False))
    assert v.cmd_wait(ns(timeout=1)) == 1


def test_watch_detects_leak_and_clean_run(net):
    v.cmd_baseline(ns(speed=False))
    assert v.cmd_watch(ns(duration=1, interval=0.3, out=None)) == 1   # real IP exposed
    net["vpn"] = True
    assert v.cmd_watch(ns(duration=1, interval=0.3, out=None)) == 0   # clean


def test_loop_drives_vpn_client(net, monkeypatch, tmp_path):
    v.cmd_baseline(ns(speed=False))

    def fake_shell(cmd):
        net["vpn"] = (cmd == "connect")
        return 0

    monkeypatch.setattr(v, "run_shell", fake_shell)
    rc = v.cmd_loop(ns(count=2, delay=0, country="DE", out="loop.json", html=None,
                       speed=False, max_speed_loss=50, connect_cmd="connect",
                       disconnect_cmd="disconnect", timeout=5))
    assert rc == 0
    assert (tmp_path / "loop_1.json").exists() and (tmp_path / "loop_2.json").exists()


def test_loop_reports_failure_when_vpn_never_comes_up(net, monkeypatch):
    v.cmd_baseline(ns(speed=False))
    monkeypatch.setattr(v, "run_shell", lambda cmd: 0)  # never flips VPN on
    rc = v.cmd_loop(ns(count=1, delay=0, country="DE", out=None, html=None,
                       speed=False, max_speed_loss=50, connect_cmd="connect",
                       disconnect_cmd=None, timeout=1))
    assert rc == 1
