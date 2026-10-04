"""Ingest a file, run a streaming query against a live NeuroFlow deployment, print the answer.

  export NEUROFLOW_URL=https://<your-app>.up.railway.app NEUROFLOW_API_KEY=<key> NEUROFLOW_PIPELINE_ID=<id>
  python sdk/examples/quickstart.py tests/fixtures/test_doc.pdf "What is this document about?"
"""
import asyncio
import os
import sys

from neuroflow import NeuroFlowClient


async def main(file_path: str, question: str) -> None:
    pipeline_id = os.environ.get("NEUROFLOW_PIPELINE_ID", "default")
    async with NeuroFlowClient(os.environ["NEUROFLOW_URL"], os.environ["NEUROFLOW_API_KEY"]) as client:
        doc = await client.ingest_file(file_path, pipeline_id)
        print(f"Ingested {doc.id} ({doc.status})\n")
        print("Streaming answer:")
        async for token in client.query(question, pipeline_id, stream=True):
            print(token, end="", flush=True)
        print()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/test_doc.pdf"
    q = sys.argv[2] if len(sys.argv) > 2 else "What is this document about?"
    asyncio.run(main(path, q))
