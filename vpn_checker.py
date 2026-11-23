#!/usr/bin/env python3
"""
vpn_checker.py - a small QA helper for testing VPN connections.

Workflow:
  1. Disconnect VPN        ->  python vpn_checker.py baseline
  2. Connect VPN (country) ->  python vpn_checker.py check --country DE
  3. Kill switch / network switch test (keep it running while you break things):
                              python vpn_checker.py watch --duration 60

Checks performed by `check`:
  - Public IP changed vs baseline
  - Country matches the one you selected (optional)
  - ISP/org changed vs baseline
  - DNS leak test (resolvers must not belong to your real ISP)
  - IPv6 leak test
  - Local DNS servers configured on the machine (informational)

Exit code is 0 if everything passes, 1 if any check FAILS (CI friendly).

Requires: pip install requests
"""

import argparse
import json
import platform
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Install it with: pip install requests")

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


def snapshot():
    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "ip_info": get_ip_info(),
        "ipv6": get_ipv6(),
        "local_dns": get_local_dns(),
    }


# ---------- commands ----------
def cmd_baseline(_args):
    print("Collecting baseline (VPN must be DISCONNECTED)...")
    snap = snapshot()
    BASELINE_FILE.write_text(json.dumps(snap, indent=2))
    info = snap["ip_info"]
    line("INFO", f"IP: {info['ip']}  Country: {info['country']}  Org: {info['org']}")
    line("INFO", f"IPv6: {snap['ipv6'] or 'none'}")
    line("INFO", f"Local DNS: {', '.join(snap['local_dns']) or 'unknown'}")
    line("PASS", f"Baseline saved to {BASELINE_FILE}")
    return 0


def load_baseline():
    if not BASELINE_FILE.exists():
        sys.exit("No baseline found. Disconnect the VPN and run: python vpn_checker.py baseline")
    return json.loads(BASELINE_FILE.read_text())


def same_org(a, b):
    """Loose comparison of two org/ASN strings."""
    if not a or not b:
        return False
    a, b = a.lower(), b.lower()
    return a in b or b in a or a.split()[0] == b.split()[0]


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

    # Summary
    fails = sum(1 for t, _ in results if t == "FAIL")
    warns = sum(1 for t, _ in results if t == "WARN")
    print(f"\nSummary: {fails} FAIL, {warns} WARN")
    print("Note: also check WebRTC manually at browserleaks.com/webrtc (needs a browser).")

    if args.out:
        Path(args.out).write_text(json.dumps({
            "time": datetime.now().isoformat(timespec="seconds"),
            "expected_country": args.country,
            "current": cur,
            "results": [{"status": t, "message": m} for t, m in results],
        }, indent=2))
        print(f"Report saved to {args.out}")
    return 1 if fails else 0


def cmd_watch(args):
    """
    Poll the public IP repeatedly. Use it while you kill the VPN process,
    switch Wi-Fi to mobile data, toggle airplane mode, etc.
      LEAK  = traffic is flowing and IP equals your real baseline IP  (BAD)
      DOWN  = no connectivity (expected when kill switch is working)
      OK    = traffic flowing through a non-baseline IP
    """
    base_ip = load_baseline()["ip_info"]["ip"]
    print(f"Watching for {args.duration}s (interval {args.interval}s). Baseline IP: {base_ip}")
    print("Now trigger your test (kill VPN, switch network, sleep/wake...). Ctrl+C to stop.\n")

    start = time.time()
    last_state = None
    leaks = 0
    log = []
    try:
        while time.time() - start < args.duration:
            t = round(time.time() - start, 1)
            try:
                ip = requests.get("https://api.ipify.org", timeout=3).text.strip()
                state = "LEAK" if ip == base_ip else "OK"
            except Exception:  # noqa: BLE001
                ip, state = None, "DOWN"
            if state == "LEAK":
                leaks += 1
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
    if args.out:
        Path(args.out).write_text(json.dumps(log, indent=2))
        print(f"Timeline saved to {args.out}")
    if leaks:
        line("FAIL", "Real IP was exposed during the test - kill switch / reconnect failed")
        return 1
    line("PASS", "Real IP was never exposed")
    return 0


def cmd_loop(args):
    """Repeat the check N times - run your connect/disconnect script between runs."""
    failures = 0
    for i in range(1, args.count + 1):
        print(f"\n===== Run {i}/{args.count} =====")
        failures += cmd_check(args)
        if i < args.count:
            time.sleep(args.delay)
    print(f"\nRuns with failures: {failures}/{args.count}")
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description="VPN QA helper: IP, DNS, IPv6 and leak checks")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("baseline", help="Record IP/DNS/IPv6 with the VPN disconnected")

    c = sub.add_parser("check", help="Verify IP/DNS/IPv6 while the VPN is connected")
    c.add_argument("--country", help="Expected 2-letter country code, e.g. DE, US, JP")
    c.add_argument("--out", help="Save JSON report to this file")

    w = sub.add_parser("watch", help="Monitor for leaks (kill switch / network switch tests)")
    w.add_argument("--duration", type=int, default=60, help="Seconds to monitor (default 60)")
    w.add_argument("--interval", type=float, default=1.0, help="Seconds between samples")
    w.add_argument("--out", help="Save timeline JSON to this file")

    l = sub.add_parser("loop", help="Run `check` repeatedly (for flakiness testing)")
    l.add_argument("--count", type=int, default=5)
    l.add_argument("--delay", type=int, default=10, help="Seconds between runs")
    l.add_argument("--country")
    l.add_argument("--out")

    args = p.parse_args()
    handlers = {"baseline": cmd_baseline, "check": cmd_check,
                "watch": cmd_watch, "loop": cmd_loop}
    try:
        sys.exit(handlers[args.cmd](args))
    except RuntimeError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
