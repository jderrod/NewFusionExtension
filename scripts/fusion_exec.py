"""Run Python inside Fusion through the add-in's dev endpoint (api.dev_exec must be true).

    py -3 scripts/fusion_exec.py -c "result = app.activeDocument.name"
    py -3 scripts/fusion_exec.py some_script.py

Globals available to the code: app, adsk, ui, engine, ops, state, cfg, paths.
Set ``result`` to return a JSON-serialisable value.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def fusion_exec(code, port=8766, timeout=600):
    req = urllib.request.Request("http://127.0.0.1:%d/api/dev/exec" % port,
                                 data=json.dumps({"code": code, "timeout": timeout}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": "HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?")
    ap.add_argument("-c", "--code")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()
    code = a.code if a.code is not None else open(a.file, encoding="utf-8").read()
    out = fusion_exec(code, a.port, a.timeout)
    if out.get("stdout"):
        sys.stdout.write(out["stdout"])
    if out.get("result") is not None:
        print(json.dumps(out["result"], indent=2, default=str))
    if out.get("error"):
        sys.stderr.write(str(out["error"]) + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
