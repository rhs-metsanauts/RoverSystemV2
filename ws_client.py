import asyncio
import websockets
import json


async def run_client():
    uri = "ws://127.0.0.1:9001"
    async with websockets.connect(uri) as ws:
        # request mapping start
        await ws.send(json.dumps({"action": "start"}))
        print("Sent start request")

        # listen for a few messages
        for _ in range(5):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                print("Received:", msg[:200])
            except asyncio.TimeoutError:
                print("No message received within timeout")

        await ws.send(json.dumps({"action": "stop"}))
        print("Sent stop request")


if __name__ == "__main__":
    asyncio.run(run_client())
