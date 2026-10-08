"""End-to-end demo against a RUNNING server: submit -> watch progress -> download the ZIP.
Usage:  python scripts/demo.py [N recipients]   (server must be running on localhost:8000)"""
import json
import subprocess
import sys
import time

import httpx

BASE, KEY = "http://localhost:8000", {"X-API-Key": "dev-secret-key"}
n = sys.argv[1] if len(sys.argv) > 1 else "200"
payload = json.loads(subprocess.check_output([sys.executable, "scripts/make_sample_request.py", n]))

with httpx.Client(base_url=BASE, headers=KEY, timeout=60) as c:
    job = c.post("/api/v1/jobs", json=payload).raise_for_status().json()
    print(f"submitted job {job['job_id']} ({job['progress']['total']} recipients)")
    while job["status"] in ("PENDING", "PROCESSING"):
        time.sleep(1)
        job = c.get(f"/api/v1/jobs/{job['job_id']}").json()
        p = job["progress"]
        print(f"  {job['status']:<22} {p['percent']:>5}%  done={p['completed']} invalid={p['invalid']} failed={p['failed']}")
    print("final status:", job["status"])
    if job["archive_url"]:
        data = c.get(job["archive_url"]).content
        open("certificates.zip", "wb").write(data)
        print(f"saved certificates.zip ({len(data)//1024} KB)")
