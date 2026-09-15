import base64
import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "capabilities":
        print(json.dumps({"ok": True, "result": {"protocol_version": 1, "backend": "fake", "plain_bip375": True, "musig2_sp": False}}), flush=True)
    elif request["op"] == "process_psbt":
        raw = base64.b64decode(request["psbt"])
        print(json.dumps({"ok": True, "result": {"psbt": base64.b64encode(raw).decode()}}), flush=True)
