import os
import asyncio
import logging
from dotenv import load_dotenv

# Load env before importing settings
load_dotenv()

from jarvis.voice.listener import ListenerStateMachine

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

async def main():
    porcupine_key = os.getenv("PORCUPINE_ACCESS_KEY")
    if not porcupine_key:
        print("Error: PORCUPINE_ACCESS_KEY not found in environment variables.")
        print("Please add it to your .env file or export it.")
        return
        
    print("Starting JARVIS Continuous Voice Loop...")
    listener = ListenerStateMachine(porcupine_key=porcupine_key)
    
    try:
        await listener.run()
    except KeyboardInterrupt:
        print("\nStopping...")
        listener.stop()

if __name__ == "__main__":
    asyncio.run(main())
