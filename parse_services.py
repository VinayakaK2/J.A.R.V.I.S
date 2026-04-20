import json

with open("render_services.json", "r", encoding="utf-16") as f:
    data = json.load(f)

for item in data:
    if "service" in item:
        svc = item["service"]
        print(f"ID: {svc.get('id')} | Name: {svc.get('name')} | Created at: {svc.get('createdAt')} | Updated at: {svc.get('updatedAt')}")
