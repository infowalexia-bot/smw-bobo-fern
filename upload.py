#!/usr/bin/env python3
"""Optional: upload out/<ep>.mp4 to YouTube as PRIVATE (you review, then publish).
Needs secrets YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN."""
import json, os, requests

meta = json.load(open("out/meta.json"))
tok = requests.post("https://oauth2.googleapis.com/token", data={
    "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
    "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}).json()["access_token"]
body = {"snippet": {"title": meta["title"][:100], "description": meta["description"],
                    "tags": meta["tags"], "categoryId": "1"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": True,
                   "containsSyntheticMedia": True}}
data = open(meta["video"], "rb").read()
r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                  headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
                           "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(len(data))},
                  data=json.dumps(body))
r.raise_for_status()
up = requests.put(r.headers["Location"], data=data, headers={"Content-Type": "video/mp4"})
print(up.status_code, up.text[:300])
up.raise_for_status()
