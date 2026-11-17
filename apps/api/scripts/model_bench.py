"""Extraction benchmark for choosing per-role models (accuracy and cost; latency ignored).

Each candidate model reads the same real pages already stored in a graph and must emit
candidate entities/claims through the same schema the agents use. We score what the recorder
would score: are the quoted excerpts verbatim in the page? Usage: uv run python scripts/model_bench.py <graph_id>
"""
import json
import sys
import time

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from openkg.agent import pricing
from openkg.agent.deep import GROUND_RULES
from openkg.config import get_settings
from openkg.db import session_scope
from openkg.domain.recorder import CandidateEntity, _excerpt_in
from openkg.models import Source

CANDIDATES = ["openai/gpt-oss-120b", "zai-org/GLM-5.3-Flash", "MiniMaxAI/MiniMax-M3", "deepseek-ai/DeepSeek-V4.1-Flash-1017",
              "Qwen/Qwen3.5-397B-A17B", "moonshotai/Kimi-K2.7", "deepseek-ai/DeepSeek-V4-Pro", "zai-org/GLM-5.3"]


class Out(BaseModel):
    entities: list[CandidateEntity] = Field(max_length=12)


def main(graph_id: str) -> None:
    st = get_settings()
    with session_scope() as s:
        pages = [(x.url, x.title, x.content) for x in s.scalars(
            select(Source).where(Source.graph_id == graph_id, func.length(Source.content) > 2500)
            .order_by(func.length(Source.content).desc()).limit(4))]
    docs = "\n\n".join(f"URL: {u}\nTITLE: {t}\nTEXT: {c[:5000]}" for u, t, c in pages)
    content = {u: c for u, _, c in pages}
    prompt = ("Record every Company, Person and Investor these pages explicitly describe, each with attribute claims "
              "(hq_location, funding_stage with valid_from date, title) that the text states. Evidence excerpts must be "
              "short VERBATIM quotes from the page at the given URL." + GROUND_RULES + "\n<pages>\n" + docs + "\n</pages>")
    print(f"{len(pages)} pages, {len(docs)} chars")
    rows = []
    for model in CANDIDATES:
        t = time.time()
        try:
            llm = ChatOpenAI(model=model, api_key=st.valar_api_key, base_url=st.valar_base_url, timeout=120, max_retries=0,
                             use_responses_api=True, store=False, streaming=True, max_tokens=16000,
                             include=["reasoning.encrypted_content"])
            res = llm.with_structured_output(Out, method="function_calling", include_raw=True).invoke(prompt)
            usage = getattr(res.get("raw"), "usage_metadata", None) or {}
            calls = getattr(res.get("raw"), "tool_calls", None) or []
            raw_ents = [x for c in calls for x in (c.get("args", {}).get("entities") or []) if isinstance(x, dict)]
            ents, invalid = [], 0
            for raw in raw_ents:  # score each entity on its own, as the recording tool effectively does per item
                try:
                    ents.append(CandidateEntity.model_validate(raw))
                except Exception:  # noqa: BLE001
                    invalid += 1
            quotes = [(e.url, e.excerpt) for ent in ents for e in [*ent.evidence, *[x for c in ent.claims for x in c.evidence]]]
            ok = sum(1 for u, q in quotes if u in content and _excerpt_in(content[u], q))
            dated = sum(1 for ent in ents for c in ent.claims if c.valid_from)
            cost, _ = pricing.estimate("valar", model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            rows.append({"model": model, "entities": len(ents), "claims": sum(len(e.claims) for e in ents), "dated": dated,
                         "quotes": len(quotes), "verbatim": ok, "rate": round(ok / len(quotes), 2) if quotes else 0,
                         "out_tok": usage.get("output_tokens", 0), "usd": round(cost, 4),
                         "usd_per_verified": round(cost / ok, 5) if ok else None, "sec": round(time.time() - t), "invalid": invalid,
                         "atomic": sum(1 for e in ents for c in e.claims if len(str(c.value)) <= 40)})
        except Exception as e:  # noqa: BLE001
            rows.append({"model": model, "error": f"{type(e).__name__}: {str(e)[:120]}", "sec": round(time.time() - t)})
        print(json.dumps(rows[-1]), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
