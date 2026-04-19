import urllib.request
import json

url = 'https://api.render.com/v1/services'
headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Authorization': 'Bearer rnd_moDkwSRJDHV5dVsyeFvB0t0AdhPI'
}
data = {
  "type": "web_service",
  "name": "jarvis-backend",
  "ownerId": "tea-d3mj8q6uk2gs73cflqcg",
  "repo": "https://github.com/VinayakaK2/J.A.R.V.I.S",
  "autoDeploy": "yes",
  "branch": "master",
  "rootDir": "jarvis",
  "serviceDetails": {
    "env": "docker",
    "envSpecificDetails": {
      "dockerContext": "."
    },
    "plan": "free"
  }
}

req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=headers, method='POST')
try:
    with urllib.request.urlopen(req) as response:
        res = json.loads(response.read().decode('utf-8'))
        print("Success!")
        print(json.dumps(res, indent=2))
except urllib.error.HTTPError as e:
    print("Error:", e.code)
    print(e.read().decode('utf-8'))
