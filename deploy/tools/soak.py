#!/usr/bin/env python3
"""Soak (load) test of the signage server - standard library only.

Scenarios running concurrently:
  a) N screens send a heartbeat (/api/tv/<dev>/playlist?heartbeat=1)
     every --hb-interval seconds with random jitter,
  b) admin: uploads small PNGs, changes settings and order, edits and deletes items,
  c) clients download /media/<file> with a Range header,
  d) occasionally /api/devices and /api/devices/<dev>/availability.

Measures latency (p50/p95/p99/max) per request type, the error rate and - if
--pid is given - the server's RSS, thread and descriptor counts from /proc
(start / middle / end + the RSS slope in the second half of the run as a leak
indicator).
Output: a table on stdout and JSON (--json-out).

Example:
  SOAK_USER=u SOAK_PASS=p python3 soak.py --url http://127.0.0.1:18090 \
      --pid 12345 --state /tmp/x/state.json --telemetry /tmp/x/telemetry.jsonl \
      --screens 30 --duration 240
"""
import argparse
import base64
import http.client
import json
import os
import random
import struct
import sys
import threading
import time
import urllib.parse
import zlib

LOCK = threading.Lock()
SAMPLES = {}       # kind -> [ms, ...]
ERRORS = {}        # kind -> {"5xx": n, "reset": n, "4xx": n, "other": n}
STATUS = {}        # kind -> {code: n}
STOP = threading.Event()
POOL = {"files": []}          # (dev, file) uploaded and not yet deleted
POOL_LOCK = threading.Lock()


def record(kind, ms, status=None, exc=None, ok_codes=(200,)):
    with LOCK:
        SAMPLES.setdefault(kind, []).append(ms)
        e = ERRORS.setdefault(kind, {"5xx": 0, "reset": 0, "4xx": 0, "other": 0})
        st = STATUS.setdefault(kind, {})
        if exc is not None:
            e["reset"] += 1
            st["exc"] = st.get("exc", 0) + 1
            return
        st[str(status)] = st.get(str(status), 0) + 1
        if status >= 500:
            e["5xx"] += 1
        elif status >= 400:
            e["4xx"] += 1
        elif status not in ok_codes:
            e["other"] += 1


class Client:
    """A single keep-alive connection; reconnects on error."""

    def __init__(self, host, port, auth=None, keepalive=True):
        self.host, self.port, self.keepalive = host, port, keepalive
        self.auth = auth
        self.conn = None

    def _connect(self):
        self.conn = http.client.HTTPConnection(self.host, self.port, timeout=30)

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None

    def request(self, kind, method, path, body=None, headers=None, ok=(200,)):
        h = dict(headers or {})
        if self.auth:
            h["Authorization"] = self.auth
        if not self.keepalive:
            h["Connection"] = "close"
        t0 = time.perf_counter()
        try:
            if self.conn is None:
                self._connect()
            self.conn.request(method, path, body=body, headers=h)
            resp = self.conn.getresponse()
            data = resp.read()
            ms = (time.perf_counter() - t0) * 1000
            if resp.will_close or not self.keepalive:
                self.close()
            record(kind, ms, status=resp.status, ok_codes=ok)
            return resp.status, data
        except (OSError, http.client.HTTPException):
            ms = (time.perf_counter() - t0) * 1000
            self.close()
            record(kind, ms, exc=True)
            return None, b""


def make_png(seed, size=160):
    """A small valid PNG with random noise (~size*size*3 bytes, incompressible)."""
    rnd = random.Random(seed)
    raw = b"".join(b"\x00" + rnd.randbytes(size * 3) for _ in range(size))

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


def multipart(filename, data):
    b = "----soak%016x" % random.getrandbits(64)
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + data + f"\r\n--{b}--\r\n".encode()
    return body, {"Content-Type": f"multipart/form-data; boundary={b}"}


def sleep_stop(sec):
    STOP.wait(max(0.0, sec))


# ---------- scenarios ----------
def heartbeat_worker(cfg, dev, idx):
    c = Client(cfg.host, cfg.port, keepalive=not cfg.no_keepalive)
    sleep_stop(random.uniform(0, cfg.hb_interval))          # spread out the start
    n = 0
    while not STOP.is_set():
        q = "heartbeat=1"
        if n % 4 == 0:
            q += "&screen=1920x1080"
        c.request("heartbeat", "GET", f"/api/tv/{dev}/playlist?{q}")
        n += 1
        j = cfg.hb_interval * cfg.jitter
        sleep_stop(cfg.hb_interval + random.uniform(-j, j))
    c.close()


def jpost(c, kind, path, obj):
    return c.request(kind, "POST", path, json.dumps(obj).encode(),
                     {"Content-Type": "application/json"})


def admin_worker(cfg, devs, wid):
    c = Client(cfg.host, cfg.port, auth=cfg.auth)
    rnd = random.Random(1000 + wid)
    seq = 0
    while not STOP.is_set():
        dev = rnd.choice(devs)
        ops = ["upload", "settings", "order", "item", "delete"]
        weights = [4, 3, 2, 2, 3]
        if cfg.admin_ops:
            keep = [i for i, o in enumerate(ops) if o in cfg.admin_ops.split(",")]
            ops, weights = [ops[i] for i in keep], [weights[i] for i in keep]
        op = rnd.choices(ops, weights)[0]
        if op == "upload":
            seq += 1
            name = f"soak{wid}_{seq}_{rnd.getrandbits(24):06x}.png"
            body, h = multipart(name, make_png(rnd.getrandbits(32), cfg.png_px))
            st, data = c.request("admin_upload", "POST",
                                 f"/api/upload?device={dev}", body, h)
            if st == 200:
                try:
                    for f in json.loads(data).get("saved", []):
                        with POOL_LOCK:
                            POOL["files"].append((dev, f))
                except ValueError:
                    pass
        elif op == "settings":
            jpost(c, "admin_settings", "/api/settings",
                  {"device": dev, "qrCorner": rnd.random() < 0.5,
                   "transition": rnd.choice(["cut", "fade"]),
                   "label": f"soak {rnd.randrange(1000)}"})
        elif op in ("order", "item", "delete"):
            with POOL_LOCK:
                mine = [f for d, f in POOL["files"] if d == dev]
            if not mine:
                continue
            if op == "order":
                rnd.shuffle(mine)
                jpost(c, "admin_order", "/api/order", {"device": dev, "order": mine})
            elif op == "item":
                jpost(c, "admin_item", "/api/item",
                      {"device": dev, "file": rnd.choice(mine),
                       "seconds": rnd.randrange(3, 30), "enabled": rnd.random() < .8})
            else:
                f = rnd.choice(mine)
                st, _ = jpost(c, "admin_delete", "/api/delete", {"device": dev, "file": f})
                if st == 200:
                    with POOL_LOCK:
                        try:
                            POOL["files"].remove((dev, f))
                        except ValueError:
                            pass
        sleep_stop(rnd.uniform(0.5, 1.5) * cfg.admin_pause)
    c.close()


def range_worker(cfg, wid):
    c = Client(cfg.host, cfg.port)
    rnd = random.Random(2000 + wid)
    while not STOP.is_set():
        with POOL_LOCK:
            pool = list(POOL["files"])
        if not pool:
            sleep_stop(1)
            continue
        _, f = rnd.choice(pool)
        a = rnd.randrange(0, 20000)
        r = rnd.choice([f"bytes={a}-{a + 4095}", f"bytes={a}-", "bytes=0-1023", "bytes=-2048"])
        # 404 = the admin deleted the file in the meantime; not a server error, but the record stays
        c.request("media_range", "GET", "/media/" + urllib.parse.quote(f),
                  headers={"Range": r}, ok=(206,))
        sleep_stop(rnd.uniform(0.2, 0.8) * cfg.range_pause)
    c.close()


def misc_worker(cfg, devs):
    c = Client(cfg.host, cfg.port, auth=cfg.auth)
    while not STOP.is_set():
        sleep_stop(random.uniform(2, 6))
        c.request("devices", "GET", "/api/devices")
        c.request("availability", "GET",
                  f"/api/devices/{random.choice(devs)}/availability?hours="
                  f"{random.choice([1, 24, 168])}")
        c.request("health", "GET", "/api/health")
    c.close()


# ---------- server measurement ----------
def proc_sample(pid):
    out = {"t": time.time()}
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    out["rss_kb"] = int(line.split()[1])
                elif line.startswith("Threads:"):
                    out["threads"] = int(line.split()[1])
        out["tasks"] = len(os.listdir(f"/proc/{pid}/task"))
        out["fds"] = len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        out["gone"] = True
    return out


def fsize(p):
    try:
        return os.path.getsize(p) if p else None
    except OSError:
        return None


def monitor(cfg, series):
    while not STOP.is_set():
        s = proc_sample(cfg.pid)
        s["telemetry_b"] = fsize(cfg.telemetry)
        s["state_b"] = fsize(cfg.state)
        series.append(s)
        sleep_stop(cfg.sample_every)


def slope(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def pct(sorted_ms, p):
    if not sorted_ms:
        return 0.0
    k = min(len(sorted_ms) - 1, max(0, int(round(p / 100 * len(sorted_ms) + 0.5)) - 1))
    return sorted_ms[k]


def summarize(series, cfg, elapsed):
    res = {"config": {k: v for k, v in vars(cfg).items() if k not in ("auth", "user", "password")},
           "elapsed_s": round(elapsed, 1), "requests": {}, "server": {}}
    for kind, ms in sorted(SAMPLES.items()):
        s = sorted(ms)
        e = ERRORS[kind]
        res["requests"][kind] = {
            "n": len(s), "p50": round(pct(s, 50), 2), "p95": round(pct(s, 95), 2),
            "p99": round(pct(s, 99), 2), "max": round(s[-1], 2),
            "err_5xx": e["5xx"], "err_reset": e["reset"], "err_4xx": e["4xx"],
            "err_other": e["other"], "status": STATUS[kind]}
    good = [s for s in series if not s.get("gone")]
    if good:
        def window(a, b):
            w = good[int(len(good) * a):max(int(len(good) * b), int(len(good) * a) + 1)]
            return {k: round(sum(x[k] for x in w if x.get(k) is not None) /
                             max(1, len([x for x in w if x.get(k) is not None])), 1)
                    for k in ("rss_kb", "threads", "fds", "telemetry_b", "state_b")
                    if any(x.get(k) is not None for x in w)}
        res["server"]["start"] = window(0, 0.1)
        res["server"]["middle"] = window(0.45, 0.55)
        res["server"]["end"] = window(0.9, 1.0)
        res["server"]["rss_peak_kb"] = max(s["rss_kb"] for s in good)
        res["server"]["threads_peak"] = max(s["threads"] for s in good)
        res["server"]["fds_peak"] = max(s["fds"] for s in good)
        half = good[len(good) // 2:]
        t0 = half[0]["t"]
        sl = slope([(s["t"] - t0) / 60 for s in half], [s["rss_kb"] for s in half])
        res["server"]["rss_slope_kb_per_min_2nd_half"] = round(sl, 1)
        res["server"]["samples"] = len(good)
    res["server"]["died"] = any(s.get("gone") for s in series)
    if good:
        t0 = good[0]["t"]
        res["series"] = [{"t": round(s["t"] - t0, 1), **{k: s.get(k) for k in (
            "rss_kb", "threads", "fds", "telemetry_b", "state_b")}} for s in good]
    return res


def print_table(res):
    print(f"\nRun time: {res['elapsed_s']} s\n")
    hdr = f"{'kind':<16}{'n':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>10}{'5xx':>6}{'reset':>7}{'4xx':>6}{'other':>6}"
    print(hdr + "   (latency in ms)")
    print("-" * len(hdr))
    for k, r in res["requests"].items():
        print(f"{k:<16}{r['n']:>7}{r['p50']:>9.1f}{r['p95']:>9.1f}{r['p99']:>9.1f}"
              f"{r['max']:>10.1f}{r['err_5xx']:>6}{r['err_reset']:>7}{r['err_4xx']:>6}{r['err_other']:>6}")
    print("\nStatus codes:")
    for k, r in res["requests"].items():
        print(f"  {k:<16}{r['status']}")
    sv = res["server"]
    if "start" in sv:
        print("\nServer (from /proc):")
        print(f"{'':<10}{'RSS MB':>9}{'threads':>8}{'fd':>6}{'telemetry B':>13}{'state B':>10}")
        for name in ("start", "middle", "end"):
            w = sv[name]
            print(f"{name:<10}{w.get('rss_kb', 0) / 1024:>9.1f}{w.get('threads', 0):>8.0f}"
                  f"{w.get('fds', 0):>6.0f}{w.get('telemetry_b', 0) or 0:>13.0f}{w.get('state_b', 0) or 0:>10.0f}")
        print(f"\nPeaks: RSS {sv['rss_peak_kb'] / 1024:.1f} MB, threads {sv['threads_peak']}, fd {sv['fds_peak']}")
        print(f"RSS slope in the 2nd half of the run: {sv['rss_slope_kb_per_min_2nd_half']} kB/min "
              f"({sv['samples']} samples)")
    if sv.get("died"):
        print("!!! The server ceased to exist during the test.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8090")
    ap.add_argument("--user", default=os.environ.get("SOAK_USER", ""))
    ap.add_argument("--password", default=os.environ.get("SOAK_PASS", ""))
    ap.add_argument("--screens", type=int, default=30, help="number of simulated screens")
    ap.add_argument("--duration", type=int, default=240, help="run time in seconds")
    ap.add_argument("--hb-interval", type=float, default=15.0, help="heartbeat period [s]")
    ap.add_argument("--jitter", type=float, default=0.2, help="period jitter (0.2 = +-20 %%)")
    ap.add_argument("--no-keepalive", action="store_true", help="heartbeat on a new connection")
    ap.add_argument("--admin-workers", type=int, default=1, help="concurrent admins (related to --concurrency)")
    ap.add_argument("--admin-pause", type=float, default=1.0, help="average pause between admin operations [s]")
    ap.add_argument("--admin-ops", default="",
                    help="restrict admin operations, e.g. upload,delete (default: all)")
    ap.add_argument("--range-clients", type=int, default=3)
    ap.add_argument("--range-pause", type=float, default=1.0)
    ap.add_argument("--png-px", type=int, default=160, help="side of the test PNG (px)")
    ap.add_argument("--concurrency", type=int, default=0,
                    help="shortcut: sets admin-workers to this value when > 0")
    ap.add_argument("--pid", type=int, default=0, help="server PID for /proc measurement")
    ap.add_argument("--state", default="", help="path to tv-state.json (size)")
    ap.add_argument("--telemetry", default="", help="path to telemetry.jsonl (size)")
    ap.add_argument("--sample-every", type=float, default=5.0)
    ap.add_argument("--json-out", default="", help="where to write the JSON result")
    cfg = ap.parse_args()
    if cfg.concurrency > 0:
        cfg.admin_workers = cfg.concurrency
    u = urllib.parse.urlparse(cfg.url)
    cfg.host, cfg.port = u.hostname, u.port or 80
    if not cfg.user or not cfg.password:
        print("Missing --user/--password (or SOAK_USER/SOAK_PASS).", file=sys.stderr)
        return 2
    cfg.auth = "Basic " + base64.b64encode(f"{cfg.user}:{cfg.password}".encode()).decode()

    # setup: screens via /api/screens (the heartbeat accepts only known devices)
    setup = Client(cfg.host, cfg.port, auth=cfg.auth)
    devs = []
    for i in range(cfg.screens):
        st, data = setup.request("setup_screen", "POST", "/api/screens",
                                 json.dumps({"label": f"soak {i}"}).encode(),
                                 {"Content-Type": "application/json"})
        if st != 200:
            print(f"Creating a screen failed (HTTP {st}); is the server running at {cfg.url}?", file=sys.stderr)
            return 1
        devs.append(json.loads(data)["device"])
    setup.close()
    with LOCK:                      # setup is not counted in the measurement
        SAMPLES.pop("setup_screen", None)
        ERRORS.pop("setup_screen", None)
        STATUS.pop("setup_screen", None)

    series = []
    threads = []

    def spawn(fn, *a):
        t = threading.Thread(target=fn, args=a, daemon=True)
        t.start()
        threads.append(t)

    if cfg.pid:
        spawn(monitor, cfg, series)
    t_start = time.time()
    for i, d in enumerate(devs):
        spawn(heartbeat_worker, cfg, d, i)
    for w in range(cfg.admin_workers):
        spawn(admin_worker, cfg, devs, w)
    for w in range(cfg.range_clients):
        spawn(range_worker, cfg, w)
    spawn(misc_worker, cfg, devs)

    try:
        while time.time() - t_start < cfg.duration:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    STOP.set()
    for t in threads:
        t.join(timeout=35)
    elapsed = time.time() - t_start
    res = summarize(series, cfg, elapsed)
    res["screens"] = len(devs)
    print_table(res)
    if cfg.json_out:
        with open(cfg.json_out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON: {cfg.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
