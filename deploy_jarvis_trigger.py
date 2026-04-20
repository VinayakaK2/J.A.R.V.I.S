import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

url = 'https://api.render.com/v1/services'
headers = {
    'Accept': 'application/json',
    'Authorization': 'Bearer rnd_moDkwSRJDHV5dVsyeFvB0t0AdhPI'
}

req = urllib.request.Request(url, headers=headers)
try:
    with urllib.request.urlopen(req, context=ctx) as response:
        data = json.loads(response.read().decode('utf-8'))
        
        target_id = None
        for item in data:
            if item.get("service", {}).get("name") == "jarvis-backend":
                target_id = item["service"]["id"]
                break
                
        if target_id:
            print("Found jarvis-backend:", target_id)
            deploy_url = f"https://api.render.com/v1/services/{target_id}/deploys"
            deploy_req = urllib.request.Request(deploy_url, data=b'', headers=headers, method='POST')
            with urllib.request.urlopen(deploy_req, context=ctx) as d_response:
                print("Deploy triggered:", d_response.read().decode('utf-8'))
        else:
            print("jarvis-backend not found in first page")
            
except Exception as e:
    print(e)
