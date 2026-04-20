import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

url = 'https://api.render.com/v1/services/srv-d7ed6ft8nd3s73e5l1p0/deploys'
headers = {
    'Accept': 'application/json',
    'Authorization': 'Bearer rnd_moDkwSRJDHV5dVsyeFvB0t0AdhPI'
}

req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req, context=ctx) as response:
        data = json.loads(response.read().decode('utf-8'))
        for d in data:
            deploy = d.get("deploy", {})
            print(f"Deploy ID: {deploy.get('id')} | Status: {deploy.get('status')} | Created at: {deploy.get('createdAt')}")
except Exception as e:
    print(e)
