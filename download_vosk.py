import os
import requests
import zipfile
import io

model_url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
models_dir = r"h:\Jarvis\models"

os.makedirs(models_dir, exist_ok=True)
print(f"Downloading {model_url}...")
response = requests.get(model_url, stream=True)
response.raise_for_status()

print("Extracting...")
with zipfile.ZipFile(io.BytesIO(response.content)) as zip_ref:
    zip_ref.extractall(models_dir)

print("Done.")
