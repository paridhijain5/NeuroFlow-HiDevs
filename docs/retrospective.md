# NeuroFlow Retrospective

## The technically hardest task

The hardest piece was the hybrid retrieval pipeline together with its evaluation loop. Dense retrieval alone was easy to get running, but real documents exposed its weakness: exact identifiers, acronyms and rare terms were often missed. Adding sparse retrieval and metadata filters fixed recall, yet the two ranked lists were scored on incompatible scales. Reciprocal Rank Fusion solved that without tuning weights, and a cross-encoder reranker then recovered precision at the top of the list. The difficulty was not any single component but making the trade-offs measurable. Every change to chunk size, fusion depth or reranker cutoff moved latency and quality in opposite directions, so I needed the evaluation harness to tell me honestly whether a change helped. Streaming responses over SSE while keeping tracing spans coherent was the second hard part, because a stream outlives the request handler that started it.

## A design decision I would change

Looking back at the ADRs, I would revisit keeping the circuit-breaker state inside each API process. It was the simplest option and worked well for a single replica, but the moment the API scales horizontally, each replica discovers a provider outage independently, and each resets on its own schedule. The runbook even has to tell an operator to reset the breaker manually, which hints that the state belongs somewhere shared, such as Redis. I would also make the retention windows configurable from the start rather than hard-coding them, since the first request from any real user is "keep my data longer".

## What production AI systems taught me

Tutorials stop at "the answer looks right". Production starts there. I learned that observability is a feature of the product: without Jaeger traces I would have guessed at latency problems, and with them the slow span was usually obvious within minutes. I learned that failure modes are mostly boring: rate limits, queue backlogs, full disks, a stale cache. Writing the runbook forced me to think about the five most likely incidents and how an engineer would diagnose each at 3 AM, and several of those thoughts turned into code, such as the health endpoint exposing queue depth and breaker state. I also learned that documentation is part of the interface. Enriching the OpenAPI spec and writing an SDK exposed awkward endpoint names and missing error descriptions that I had stopped noticing as the author.

## What the metric improvement sprint taught me

The Task 48 sprint changed how I approach quality work. My first instinct was to change several things at once, which made it impossible to know what had worked. Once I moved to one change per experiment, logged in MLflow with the evaluation scores attached, progress became steady and, more importantly, explainable. I also learned to distrust a single headline number. A model can improve its average score while regressing on a category of queries, so I now look at the distribution and at failure examples, not just the mean. Finally, I learned that the quality of the input matters more than any tuning: several of the biggest gains came from cleaning ingested documents and training data rather than from touching the model. If I started again, I would build the evaluation harness first, before any retrieval code, and let it guide every decision after that.
