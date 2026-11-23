# Changelog

## 1.1.0
- Added speed and latency comparison (`baseline --speed`, `check --speed`, `speed` command)
- Added `wait` command to measure VPN connect time
- `loop` can now drive the VPN client with `--connect-cmd` / `--disconnect-cmd` and reports connect-time statistics
- `watch` now tracks outage duration and recovery
- Added HTML report output (`check --html`)
- Per-run JSON reports in `loop`
- Added automated tests and a GitHub Actions workflow

## 1.0.0
- Initial release: `baseline`, `check`, `watch`, `loop`
- IP, country, ISP, DNS leak, IPv6 leak, and local DNS checks
