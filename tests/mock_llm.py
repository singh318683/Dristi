"""Tiny OpenAI-compatible mock used only to test streaming end-to-end (no real AI)."""
import json, asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
app = FastAPI()
LAST = {}

@app.post("/v1/chat/completions")
async def cc(req: Request):
    body = await req.json(); LAST["body"] = body
    sys_json = body["messages"][1]["content"]
    chart = json.loads(sys_json.split("\n", 1)[1])
    md = next(r["lord"] for r in chart["vimshottari"]["mahadasha"] if r["status"] == "now")
    text = (f"**Namaste {chart['person']['name']}.** Mock answer. Lagna {chart['lagna']['sign']}, "
            f"Moon {chart['moon_sign']}, running {md} mahadasha.\n- point one\n- point two\n_Disclaimer._")
    async def gen():
        for w in text.split(" "):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": w + " "}}]}) + "\n\n"
            await asyncio.sleep(0.01)
        yield "data: [DONE]\n\n"
    if not body.get("stream"):
        return {"choices": [{"message": {"content": text}}]}
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/last")
def last(): return LAST
