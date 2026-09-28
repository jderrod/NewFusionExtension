"""Live test: drop an order into the inbox and follow it through the real add-in.

    py -3 tests/live/run_order.py path/to/order.json [--timeout 1800]

Exits 0 when the order lands in dropbox/done with every PDF present.
"""

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
API = "http://127.0.0.1:8766"


def get(path):
    with urllib.request.urlopen(API + path, timeout=30) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("order")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--name", default=None, help="file name to use in the inbox")
    a = ap.parse_args()

    evs = get("/api/events?since=0")["events"]
    since = evs[-1]["seq"] if evs else 0

    name = a.name or ("live_%s_%s" % (time.strftime("%H%M%S"), os.path.basename(a.order)))
    dst = os.path.join(ROOT, "dropbox", "inbox", name)
    shutil.copy(a.order, dst + ".tmp")
    os.replace(dst + ".tmp", dst)
    print("dropped", name)
    stem = os.path.splitext(name)[0]
    t0 = time.time()
    folder = None
    health_samples = []
    while time.time() - t0 < a.timeout:
        time.sleep(2)
        for ev in get("/api/events?since=%d" % since)["events"]:
            since = ev["seq"]
            if ev["level"] != "debug":
                print("%7.1fs %-5s %s" % (time.time() - t0, ev["level"], ev["msg"]))
            else:
                print("%7.1fs        . %s" % (time.time() - t0, ev["msg"]))
        st = get("/api/status")
        h = st.get("health") or {}
        health_samples.append((round(time.time() - t0), h.get("threads"), h.get("working_set_mb"),
                               h.get("documents")))
        for f in ("done", "failed"):
            if os.path.exists(os.path.join(ROOT, "dropbox", f, stem + ".report.json")):
                folder = f
        if folder:
            break
    if not folder:
        print("TIMEOUT")
        return 2
    rep = json.load(open(os.path.join(ROOT, "dropbox", folder, stem + ".report.json")))
    print("\nRESULT:", folder, json.dumps(rep["summary"]))
    ok = folder == "done"
    for c in rep["components"]:
        pdf = c.get("pdf")
        exists = bool(pdf) and os.path.isfile(os.path.join(ROOT, "output", pdf))
        steps = ", ".join("%s %.1fs" % (s["name"], s.get("duration_s") or 0) for s in c["steps"])
        print(" %-4s %-7s v%-5s pdf=%s(%s) %s" % (c["id"], c["status"], c.get("model_version"), pdf,
                                                 c.get("pdf_bytes"), "ok" if exists else "MISSING"))
        print("      steps:", steps)
        for w in c.get("warnings") or []:
            print("      warn:", w)
        if c.get("error"):
            print("      error:", c["error"])
        ok = ok and exists
    print("health (t, threads, MB, docs):", health_samples[::max(1, len(health_samples) // 12)])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
