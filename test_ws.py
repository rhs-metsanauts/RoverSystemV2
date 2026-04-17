import websocket

def on_open(ws):
    print("connected")

def on_message(ws, m):
    print("msg:", str(m)[:80])

def on_error(ws, e):
    print("error:", e)

def on_close(ws, c, m):
    print("closed:", c, m)

ws = websocket.WebSocketApp(
    "ws://192.168.55.1:9001",
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close,
)
ws.run_forever()
