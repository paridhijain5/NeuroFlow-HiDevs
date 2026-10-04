"""Single place to wire real dependencies (used by worker + calibration)."""
from .llm import RouterLLM


def build_llm_and_embedder():
    """ADAPT: return (llm, embedder) built from your existing ModelRouter and embedding client."""
    raise NotImplementedError(
        "Wire your ModelRouter + embedding client here, e.g.\n"
        "  from backend.models.router import ModelRouter\n"
        "  llm = RouterLLM(ModelRouter())\n"
        "  embedder = <object with async embed(list[str]) -> list[list[float]]>"
    )
