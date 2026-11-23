#!/usr/bin/env python3
"""
vpn_checker.py - a small QA helper for testing VPN connections.

Workflow:
  1. Disconnect VPN        ->  python vpn_checker.py baseline --speed
  2. Connect VPN (country) ->  python vpn_checker.py check --country DE --speed
  3. Kill switch / network switch test (keep it running while you break things):
                              python vpn_checker.py watch --duration 60
  4. Measure connect time  ->  python vpn_checker.py wait   (then press Connect)
  5. Automated matrix      ->  python vpn_checker.py loop --count 10 \\
                                 --connect-cmd "wg-quick up wg0" \\
                                 --disconnect-cmd "wg-quick down wg0"

Checks performed by `check`:
  - Public IP changed vs baseline
  - Country matches the one you selected (optional)
  - ISP/org changed vs baseline
  - DNS leak test (resolvers must not belong to your real ISP)
  - IPv6 leak test
  - Local DNS servers configured on the machine (informational)
  - Speed / latency vs baseline (optional, --speed)

Exit code is 0 if everything passes, 1 if any check FAILS (CI friendly).

Requires: pip install requests
"""

import argparse
import copy
import html
import json
import platform
import re
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Install it with: pip install requests")

VERSION = "1.1.0"
BASELINE_FILE = Path("vpn_baseline.json")
TIMEOUT = 8

# ---------- output helpers ----------
USE_COLOR = sys.stdout.isatty()
COLORS = {"PASS": "\033[92m", "FAIL": "\033[91m", "WARN": "\033[93m", "INFO": "\033[96m"}


def color(tag):
    if not USE_COLOR:
        return tag
    return f"{COLORS.get(tag, '')}{tag}\033[0m"


def line(tag, msg):
    print(f"[{color(tag)}] {msg}")


# ---------- data collection ----------
def get_ip_info():
    """Return dict with ip, country, city, org. Tries multiple providers."""
    providers = [
        ("https://ipinfo.io/json", lambda d: {
            "ip": d.get("ip"), "country": d.get("country"),
            "city": d.get("city"), "org": d.get("org")}),
        ("https://ipapi.co/json/", lambda d: {
            "ip": d.get("ip"), "country": d.get("country_code"),
            "city": d.get("city"), "org": d.get("org")}),
    ]
    last_err = None
    for url, parse in providers:
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            info = parse(r.json())
            if info.get("ip"):
                return info
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Could not get public IP info: {last_err}")


def get_ip_simple(timeout=3):
    """Fast public-IP lookup used by the polling commands."""
    return requests.get("https://api.ipify.org", timeout=timeout).text.strip()


def get_ipv6():
    """Return public IPv6 address, or None if there is no IPv6 connectivity."""
    try:
        r = requests.get("https://api6.ipify.org", timeout=5)
        r.raise_for_status()
        return r.text.strip()
    except Exception:  # noqa: BLE001
        return None


def get_local_dns():
    """Return DNS servers configured on this machine (best effort)."""
    system = platform.system()
    servers = []
    ip_re = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]{3,}(?::[0-9a-fA-F]+)+)")

    def run(cmd):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout

    try:
        if system == "Windows":
            in_dns = False
            for ln in run(["ipconfig", "/all"]).splitlines():
                if "DNS Servers" in ln:
                    in_dns = True
                    servers += ip_re.findall(ln.split(":", 1)[-1])
                elif in_dns and ln.startswith(" " * 20):
                    servers += ip_re.findall(ln)
                else:
                    in_dns = False
        elif system == "Darwin":
            for ln in run(["scutil", "--dns"]).splitlines():
                if "nameserver[" in ln:
                    servers += ip_re.findall(ln.split(":", 1)[-1])
        else:
            try:
                for ln in run(["resolvectl", "status"]).splitlines():
                    if "DNS Server" in ln:
                        servers += ip_re.findall(ln.split(":", 1)[-1])
            except FileNotFoundError:
                pass
            if not servers:
                for ln in Path("/etc/resolv.conf").read_text().splitlines():
                    if ln.startswith("nameserver"):
                        servers += ip_re.findall(ln)
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(servers))  # de-dupe, keep order


def dns_leak_test():
    """
    Uses the bash.ws DNS leak API: we trigger DNS lookups of unique hostnames,
    then ask the API which resolvers actually reached its authoritative server.
    Returns list of dicts: {ip, country_name, asn}.
    """
    test_id = requests.get("https://bash.ws/id", timeout=TIMEOUT).text.strip()
    for i in range(1, 11):
        try:
            socket.gethostbyname(f"{i}.{test_id}.bash.ws")
        except socket.gaierror:
            pass
    time.sleep(1)
    data = requests.get(f"https://bash.ws/dnsleak/test/{test_id}?json", timeout=15).json()
    return [d for d in data if d.get("type") == "dns"]


def measure_latency(samples=5):
    """Median HTTPS round-trip time in ms (first request is a warm-up)."""
    url = "https://www.cloudflare.com/cdn-cgi/trace"
    times = []
    with requests.Session() as s:
        s.get(url, timeout=TIMEOUT)  # warm-up: DNS + TCP + TLS
        for _ in range(samples):
            t = time.perf_counter()
            s.get(url, timeout=TIMEOUT)
            times.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(times), 1)


def measure_speed(size_mb=10):
    """Download speed in Mbps using Cloudflare's speed endpoint."""
    url = f"https://speed.cloudflare.com/__down?bytes={size_mb * 1_000_000}"
    total = 0
    start = time.perf_counter()
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        for chunk in r.iter_content(65536):
            total += len(chunk)
    secs = max(time.perf_counter() - start, 1e-6)
    return round(total * 8 / secs / 1e6, 2)


def speed_snapshot(size_mb=10):
    return {"mbps": measure_speed(size_mb), "latency_ms": measure_latency()}


def snapshot(with_speed=False):
    snap = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "ip_info": get_ip_info(),
        "ipv6": get_ipv6(),
        "local_dns": get_local_dns(),
    }
    if with_speed:
        snap["speed"] = speed_snapshot()
    return snap


def run_shell(cmd):
    """Run a user-supplied shell command; returns its exit code."""
    print(f"  $ {cmd}")
    return subprocess.run(cmd, shell=True).returncode


def wait_for_vpn(base_ip, timeout, interval=0.5):
    """Poll until the public IP differs from base_ip. Returns (seconds, ip) or (None, None)."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            ip = get_ip_simple()
            if ip != base_ip:
                return round(time.time() - start, 1), ip
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return None, None


# ---------- reports ----------
HTML_CSS = """
body{font-family:system-ui,Arial,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
h1{font-size:1.4rem} table{border-collapse:collapse;width:100%;margin:1rem 0}
td,th{border:1px solid #ccc;padding:.5rem .7rem;text-align:left}
td.PASS{background:#d4f4dd;font-weight:600} td.FAIL{background:#f8d0d0;font-weight:600}
td.WARN{background:#fdf0c4;font-weight:600} td.INFO{background:#dbeafe;font-weight:600}
.meta{color:#555;font-size:.9rem}
"""


def write_html(path, report):
    rows = "".join(
        f"<tr><td class='{html.escape(r['status'])}'>{html.escape(r['status'])}</td>"
        f"<td>{html.escape(r['message'])}</td></tr>"
        for r in report["results"]
    )
    cur = report["current"]
    meta = (f"IP {html.escape(str(cur['ip']))} &middot; {html.escape(str(cur['country']))} "
            f"&middot; {html.escape(str(cur['city']))} &middot; {html.escape(str(cur['org']))}")
    fails = sum(1 for r in report["results"] if r["status"] == "FAIL")
    verdict = "FAILED" if fails else "PASSED"
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'><title>VPN check report</title>"
        f"<style>{HTML_CSS}</style></head><body>"
        f"<h1>VPN check report: {verdict}</h1>"
        f"<p class='meta'>{html.escape(report['time'])} &middot; expected country: "
        f"{html.escape(str(report['expected_country'] or 'not set'))}<br>{meta}</p>"
        f"<table><tr><th>Status</th><th>Detail</th></tr>{rows}</table>"
        f"<p class='meta'>Generated by vpn_checker.py v{VERSION}</p></body></html>"
    )
    Path(path).write_text(doc, encoding="utf-8")


def same_org(a, b):
    """Loose comparison of two org/ASN strings."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a in b or b in a or a.split()[0] == b.split()[0]


def load_baseline():
    if not BASELINE_FILE.exists():
        sys.exit("No baseline found. Disconnect the VPN and run: python vpn_checker.py baseline")
    return json.loads(BASELINE_FILE.read_text())


# ---------- commands ----------
def cmd_baseline(args):
    print("Collecting baseline (VPN must be DISCONNECTED)...")
    snap = snapshot(with_speed=args.speed)
    BASELINE_FILE.write_text(json.dumps(snap, indent=2))
    info = snap["ip_info"]
    line("INFO", f"IP: {info['ip']}  Country: {info['country']}  Org: {info['org']}")
    line("INFO", f"IPv6: {snap['ipv6'] or 'none'}")
    line("INFO", f"Local DNS: {', '.join(snap['local_dns']) or 'unknown'}")
    if "speed" in snap:
        line("INFO", f"Speed: {snap['speed']['mbps']} Mbps  Latency: {snap['speed']['latency_ms']} ms")
    line("PASS", f"Baseline saved to {BASELINE_FILE}")
    return 0


def cmd_check(args):
    base = load_baseline()
    base_info = base["ip_info"]
    results = []  # (tag, message)

    def add(tag, msg):
        results.append((tag, msg))
        line(tag, msg)

    print(f"Baseline taken at {base['time']}\n")

    # 1. IP
    cur = get_ip_info()
    line("INFO", f"Current IP: {cur['ip']}  Country: {cur['country']}  "
                 f"City: {cur['city']}  Org: {cur['org']}")
    if cur["ip"] == base_info["ip"]:
        add("FAIL", "Public IP did NOT change - VPN traffic is not being tunneled")
    else:
        add("PASS", "Public IP changed")

    # 2. Country
    if args.country:
        if (cur["country"] or "").upper() == args.country.upper():
            add("PASS", f"Country matches expected ({args.country.upper()})")
        else:
            add("FAIL", f"Country mismatch: expected {args.country.upper()}, got {cur['country']}")
    else:
        add("INFO", "No --country given, skipping country check")

    # 3. Org / ISP
    if same_org(cur["org"], base_info["org"]):
        add("FAIL", f"Org still looks like your real ISP ({cur['org']})")
    else:
        add("PASS", "Org/ISP differs from baseline")

    # 4. DNS leak
    print("\nRunning DNS leak test (takes a few seconds)...")
    try:
        resolvers = dns_leak_test()
        if not resolvers:
            add("WARN", "DNS leak API returned no resolvers")
        for r in resolvers:
            line("INFO", f"Resolver {r.get('ip')}  {r.get('country_name')}  {r.get('asn')}")
        leaked = [r for r in resolvers if same_org(r.get("asn"), base_info["org"])]
        if leaked:
            add("FAIL", "DNS LEAK: resolver(s) belong to your real ISP: "
                        + ", ".join(r["ip"] for r in leaked))
        elif any((r.get("country_name") or "").lower().startswith(
                    (base_info.get("country") or "~~~").lower()) for r in resolvers):
            add("WARN", "Resolver in your real country - verify manually it is the VPN's DNS")
        elif resolvers:
            add("PASS", "No DNS leak detected")
    except Exception as e:  # noqa: BLE001
        add("WARN", f"DNS leak test could not run: {e}")

    # 5. Local DNS config
    local = get_local_dns()
    line("INFO", f"Local DNS servers: {', '.join(local) or 'unknown'}")
    leaked_local = [d for d in local if d in base["local_dns"] and not d.startswith(("127.", "::1"))]
    if leaked_local:
        add("WARN", "Local DNS config still has your original servers: " + ", ".join(leaked_local))
    else:
        add("PASS", "Local DNS config differs from baseline")

    # 6. IPv6
    v6 = get_ipv6()
    if v6 is None:
        add("PASS", "No IPv6 connectivity (blocked or unavailable) - no IPv6 leak")
    elif v6 == base["ipv6"]:
        add("FAIL", f"IPv6 LEAK: real IPv6 address exposed ({v6})")
    else:
        add("WARN", f"IPv6 reachable ({v6}) and differs from baseline - confirm it is the VPN's")

    # 7. Speed / latency (optional)
    if getattr(args, "speed", False):
        print("\nRunning speed test...")
        try:
            snap = speed_snapshot()
            line("INFO", f"VPN speed: {snap['mbps']} Mbps  Latency: {snap['latency_ms']} ms")
            b = base.get("speed")
            if b and b.get("mbps"):
                loss = (b["mbps"] - snap["mbps"]) / b["mbps"] * 100
                msg = (f"Speed change vs baseline: {-loss:+.0f}% "
                       f"({b['mbps']} -> {snap['mbps']} Mbps, "
                       f"latency {b['latency_ms']} -> {snap['latency_ms']} ms)")
                if loss > args.max_speed_loss:
                    add("FAIL", f"{msg} - exceeds allowed loss of {args.max_speed_loss:.0f}%")
                else:
                    add("PASS", msg)
            else:
                add("INFO", "No baseline speed stored - run `baseline --speed` to enable comparison")
        except Exception as e:  # noqa: BLE001
            add("WARN", f"Speed test could not run: {e}")

    # Summary
    fails = sum(1 for t, _ in results if t == "FAIL")
    warns = sum(1 for t, _ in results if t == "WARN")
    print(f"\nSummary: {fails} FAIL, {warns} WARN")
    print("Note: also check WebRTC manually at browserleaks.com/webrtc (needs a browser).")

    report = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "expected_country": args.country,
        "current": cur,
        "results": [{"status": t, "message": m} for t, m in results],
    }
    if getattr(args, "out", None):
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"JSON report saved to {args.out}")
    if getattr(args, "html", None):
        write_html(args.html, report)
        print(f"HTML report saved to {args.html}")
    return 1 if fails else 0


def cmd_watch(args):
    """
    Poll the public IP repeatedly. Use it while you kill the VPN process,
    switch Wi-Fi to mobile data, toggle airplane mode, etc.
      LEAK  = traffic is flowing and IP equals your real baseline IP  (BAD)
      DOWN  = no connectivity (expected when kill switch is working)
      OK    = traffic flowing through a non-baseline IP
    Also reports how long each outage lasted (time to recover).
    """
    base_ip = load_baseline()["ip_info"]["ip"]
    print(f"Watching for {args.duration}s (interval {args.interval}s). Baseline IP: {base_ip}")
    print("Now trigger your test (kill VPN, switch network, sleep/wake...). Ctrl+C to stop.\n")

    start = time.time()
    last_state = None
    down_since = None
    outages = []
    leaks = 0
    log = []
    try:
        while time.time() - start < args.duration:
            t = round(time.time() - start, 1)
            try:
                ip = get_ip_simple()
                state = "LEAK" if ip == base_ip else "OK"
            except Exception:  # noqa: BLE001
                ip, state = None, "DOWN"
            if state == "LEAK":
                leaks += 1
            if state == "DOWN" and down_since is None:
                down_since = t
            if state == "OK" and down_since is not None:
                outages.append(round(t - down_since, 1))
                line("INFO", f"Recovered after {outages[-1]}s outage")
                down_since = None
            log.append({"t": t, "state": state, "ip": ip})
            if state != last_state:  # only print on state change
                tag = {"LEAK": "FAIL", "DOWN": "WARN", "OK": "PASS"}[state]
                line(tag, f"t={t:>6}s  {state:<4} ip={ip}")
                last_state = state
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped by user.")

    print(f"\nSamples: {len(log)} | OK: {sum(x['state']=='OK' for x in log)} | "
          f"DOWN: {sum(x['state']=='DOWN' for x in log)} | LEAK: {leaks}")
    if outages:
        print(f"Outages: {len(outages)} | longest: {max(outages)}s | "
              f"average: {round(sum(outages)/len(outages), 1)}s")
    if down_since is not None:
        line("WARN", "Connection was still down when the watch ended (never recovered)")
    if args.out:
        Path(args.out).write_text(json.dumps(log, indent=2))
        print(f"Timeline saved to {args.out}")
    if leaks:
        line("FAIL", "Real IP was exposed during the test - kill switch / reconnect failed")
        return 1
    line("PASS", "Real IP was never exposed")
    return 0


def cmd_wait(args):
    """Measure how long the VPN takes to connect: run this, then click Connect."""
    base_ip = load_baseline()["ip_info"]["ip"]
    print(f"Waiting up to {args.timeout}s for the public IP to change from {base_ip}.")
    print("Start your VPN connection NOW...")
    secs, ip = wait_for_vpn(base_ip, args.timeout)
    if secs is None:
        line("FAIL", f"IP did not change within {args.timeout}s - VPN failed to connect")
        return 1
    line("PASS", f"Tunnel up: IP changed to {ip} after {secs}s")
    return 0


def cmd_speed(args):
    """Standalone speed and latency test, compared with baseline if available."""
    print("Running speed test...")
    snap = {"mbps": measure_speed(args.size), "latency_ms": measure_latency()}
    line("INFO", f"Speed: {snap['mbps']} Mbps  Latency: {snap['latency_ms']} ms")
    if BASELINE_FILE.exists():
        b = json.loads(BASELINE_FILE.read_text()).get("speed")
        if b and b.get("mbps"):
            change = (snap["mbps"] - b["mbps"]) / b["mbps"] * 100
            line("INFO", f"Baseline: {b['mbps']} Mbps / {b['latency_ms']} ms  "
                         f"(speed change {change:+.0f}%)")
        else:
            line("INFO", "No baseline speed stored (run `baseline --speed` while VPN is off)")
    return 0


def cmd_loop(args):
    """
    Repeat the check N times.
    With --connect-cmd / --disconnect-cmd the tool drives your VPN client itself:
      connect -> wait for IP change (connect time) -> check -> disconnect
    """
    base_ip = load_baseline()["ip_info"]["ip"]
    failures = 0
    connect_times = []

    for i in range(1, args.count + 1):
        print(f"\n===== Run {i}/{args.count} =====")
        run_failed = False

        if args.connect_cmd:
            started = time.time()
            rc = run_shell(args.connect_cmd)
            if rc != 0:
                line("WARN", f"Connect command exited with code {rc}")
            remaining = max(args.timeout - (time.time() - started), 1)
            secs, ip = wait_for_vpn(base_ip, remaining)
            if secs is None:
                line("FAIL", f"VPN did not come up within {args.timeout}s")
                run_failed = True
            else:
                total = round(time.time() - started, 1)
                connect_times.append(total)
                line("INFO", f"Connected in {total}s (IP {ip})")

        if not run_failed:
            run_args = copy.copy(args)
            run_args.html = getattr(args, "html", None)
            if args.out and args.count > 1:
                p = Path(args.out)
                run_args.out = str(p.with_name(f"{p.stem}_{i}{p.suffix}"))
            if cmd_check(run_args) != 0:
                run_failed = True

        if args.disconnect_cmd:
            run_shell(args.disconnect_cmd)

        failures += 1 if run_failed else 0
        if i < args.count:
            time.sleep(args.delay)

    print("\n===== Loop summary =====")
    print(f"Runs with failures: {failures}/{args.count}")
    if connect_times:
        print(f"Connect time: min {min(connect_times)}s | "
              f"avg {round(sum(connect_times)/len(connect_times), 1)}s | "
              f"max {max(connect_times)}s")
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description="VPN QA helper: IP, DNS, IPv6, leak and speed checks")
    p.add_argument("--version", action="version", version=f"vpn_checker {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("baseline", help="Record IP/DNS/IPv6 with the VPN disconnected")
    b.add_argument("--speed", action="store_true", help="Also record speed and latency")

    c = sub.add_parser("check", help="Verify IP/DNS/IPv6 while the VPN is connected")
    c.add_argument("--country", help="Expected 2-letter country code, e.g. DE, US, JP")
    c.add_argument("--out", help="Save JSON report to this file")
    c.add_argument("--html", help="Save an HTML report to this file")
    c.add_argument("--speed", action="store_true", help="Also run speed/latency comparison")
    c.add_argument("--max-speed-loss", type=float, default=50.0,
                   help="Max allowed speed drop in percent before FAIL (default 50)")

    w = sub.add_parser("watch", help="Monitor for leaks (kill switch / network switch tests)")
    w.add_argument("--duration", type=int, default=60, help="Seconds to monitor (default 60)")
    w.add_argument("--interval", type=float, default=1.0, help="Seconds between samples")
    w.add_argument("--out", help="Save timeline JSON to this file")

    wt = sub.add_parser("wait", help="Measure VPN connect time (run, then click Connect)")
    wt.add_argument("--timeout", type=int, default=60, help="Give up after N seconds")

    s = sub.add_parser("speed", help="Run a speed and latency test")
    s.add_argument("--size", type=int, default=10, help="Download size in MB (default 10)")

    l = sub.add_parser("loop", help="Run `check` repeatedly (optionally driving the VPN client)")
    l.add_argument("--count", type=int, default=5)
    l.add_argument("--delay", type=int, default=10, help="Seconds between runs")
    l.add_argument("--country")
    l.add_argument("--out", help="JSON report path (suffixed _1, _2... when count > 1)")
    l.add_argument("--speed", action="store_true")
    l.add_argument("--max-speed-loss", type=float, default=50.0)
    l.add_argument("--connect-cmd", help="Shell command that connects the VPN")
    l.add_argument("--disconnect-cmd", help="Shell command that disconnects the VPN")
    l.add_argument("--timeout", type=int, default=60,
                   help="Seconds to wait for the VPN to come up after --connect-cmd")

    args = p.parse_args()
    handlers = {"baseline": cmd_baseline, "check": cmd_check, "watch": cmd_watch,
                "wait": cmd_wait, "speed": cmd_speed, "loop": cmd_loop}
    try:
        sys.exit(handlers[args.cmd](args))
    except RuntimeError as e:
        sys.exit(f"Error: {e}")
    except requests.RequestException as e:
        sys.exit(f"Network error: {e}")


if __name__ == "__main__":
    main()
