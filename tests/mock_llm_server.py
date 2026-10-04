"""Tiny controllable fake LLM provider for the circuit-breaker test (Test 3).

Start NeuroFlow's TEST instance with its LLM base URL pointing here, e.g.
    LLM_BASE_URL=http://localhost:9999/v1   (use whatever env var your Task 40 code reads)
Then run:  python -m tests.mock_llm_server --port 9999
Control:   POST /__control {"mode": "fail" | "ok"}
"""
import argparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
state = {"mode": "ok"}


@app.get("/__control")
async def get_mode():
    return state


@app.post("/__control")
async def set_mode(request: Request):
    body = await request.json()
    if body.get("mode") not in ("fail", "ok"):
        return JSONResponse({"error": "mode must be fail|ok"}, status_code=400)
    state["mode"] = body["mode"]
    return state


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def anything(path: str):
    if state["mode"] == "fail":
        return JSONResponse({"error": {"message": "mock provider failure"}}, status_code=500)
    text = "This is a mock completion used for testing. " * 3
    return {
        "id": "mock-1", "object": "chat.completion", "model": "mock",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    uvicorn.run(app, host="0.0.0.0", port=ap.parse_args().port)
