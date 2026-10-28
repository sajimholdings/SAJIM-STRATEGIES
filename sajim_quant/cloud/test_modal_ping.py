import modal

app = modal.App("test-ping")

@app.function()
def square(x: int) -> int:
    return x * x

@app.local_entrypoint()
def main():
    print("[PING] Sending square(42) to Modal Cloud...", flush=True)
    res = square.remote(42)
    print(f"[PONG] Result from Modal: {res}", flush=True)
