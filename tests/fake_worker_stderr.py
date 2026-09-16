import base64
import json
import sys


print("booting fake worker", file=sys.stderr, flush=True)

for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "capabilities":
        print("handling capabilities", file=sys.stderr, flush=True)
        print(json.dumps({"ok": True, "result": {"protocol_version": 1, "backend": "fake", "plain_bip375": True, "musig2_sp": False}}), flush=True)
    elif request["op"] == "process_psbt":
        print("handling process_psbt", file=sys.stderr, flush=True)
        raw = base64.b64decode(request["psbt"])
        print(json.dumps({"ok": True, "result": {"psbt": base64.b64encode(raw).decode()}}), flush=True)
