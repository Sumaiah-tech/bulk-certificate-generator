"""Print a sample request with N recipients (every 50th is deliberately invalid).
Usage:  python scripts/make_sample_request.py 500 > big.json"""
import json
import sys

n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
recipients = [
    {"name": f"Participant {i:04d}", "email": ("not-an-email" if i % 50 == 0 else f"person{i}@example.com")}
    for i in range(1, n + 1)
]
print(json.dumps({"event_name": "Load Test Workshop", "issuer": "DSCE Coding Club",
                  "issue_date": "2026-10-08", "recipients": recipients}, indent=1))
