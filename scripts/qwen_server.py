"""Local, cached Qwen runtime for Sentinel's bounded chat completion contract."""

import argparse
from functools import lru_cache
from threading import Lock

from fastapi import FastAPI, HTTPException
from pydantic import Field

from sentinel.config import PLANNER_MODEL
from sentinel.tasks import StrictModel


class Message(StrictModel):
    role: str = Field(max_length=10)
    content: str = Field(max_length=8000)


class Completion(StrictModel):
    model: str = Field(max_length=150)
    messages: list[Message] = Field(min_length=2, max_length=2)
    max_tokens: int = Field(ge=1, le=72)
    temperature: int


lock = Lock()
app = FastAPI(docs_url=None, redoc_url=None)


@lru_cache(maxsize=1)
def load():
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.set_num_threads(4)
    tokenizer = AutoTokenizer.from_pretrained(PLANNER_MODEL, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(
        PLANNER_MODEL,
        local_files_only=True,
        torch_dtype="auto",
    ).eval()
    return tokenizer, model


@app.post("/v1/chat/completions")
def complete(body: Completion):
    import torch

    if body.model != PLANNER_MODEL:
        raise HTTPException(422, "This local runtime serves the cached Sentinel Qwen model.")
    if not lock.acquire(blocking=False):
        raise HTTPException(503, "The local model is busy.")
    try:
        tokenizer, model = load()
        inputs = tokenizer.apply_chat_template(
            [message.model_dump() for message in body.messages],
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=body.max_tokens,
                max_time=35,
            )
        answer = tokenizer.decode(output[0, inputs.input_ids.shape[1] :], skip_special_tokens=True)
        return {"choices": [{"message": {"content": answer}}]}
    finally:
        lock.release()


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    load()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
