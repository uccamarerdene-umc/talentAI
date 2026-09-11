"""
graphrag_engine.py — GraphRAG local search wrapper for main_api.py

main_api.py-ийн get_local_search_engine() энэ модулиас
build_local_search_engine()-г импортолж, буцаж ирсэн объектын
синхрон .search(query) методыг дуудна (үр дүн: .response талбартай объект).

GraphRAG-ийн Python API (graphrag.api.local_search) бүрэн async бөгөөд
GraphRagConfig + индексийн parquet dataframe-үүдийг шаарддаг тул
эдгээрийг backend/settings.yaml болон backend/output/-оос нэг удаа
ачаалаад кэшилнэ (индекс болгонд дахин уншихгүй).
"""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import litellm
import graphrag.api as api
from graphrag.cli.query import _resolve_output_files
from graphrag.config.load_config import load_config

logger = logging.getLogger("talent_ai.graphrag")

BACKEND_ROOT = Path(__file__).resolve().parent
COMMUNITY_LEVEL = 2
RESPONSE_TYPE = "Multiple Paragraphs"

_OUTPUT_TABLES = ["communities", "community_reports", "text_units", "relationships", "entities"]
_OPTIONAL_TABLES = ["covariates"]


def _patch_litellm_embedding_index_bug() -> None:
    """Gemini-ийн OpenAI-нийцтэй embeddings endpoint нь хариу дахь
    `data[i].index`-г бөглөдөггүй (null буцаадаг). graphrag_llm сан үүнийг
    заавал int байх ёстой гэж pydantic-аар шалгадаг тул
    "Input should be a valid integer" алдаа өгдөг.
    litellm.embedding()-ийг wrap хийж дутуу index-үүдийг байрлалаар нь
    бөглөж график graphrag_llm-д хүрэхээс өмнө засна.
    """
    if getattr(litellm.embedding, "_talent_ai_index_patched", False):
        return

    original_embedding = litellm.embedding

    def patched_embedding(*args, **kwargs):
        response = original_embedding(*args, **kwargs)
        try:
            for i, item in enumerate(response.data):
                if item.get("index") is None:
                    item["index"] = i
        except Exception:
            pass
        return response

    patched_embedding._talent_ai_index_patched = True
    litellm.embedding = patched_embedding


_patch_litellm_embedding_index_bug()


class LocalSearchEngine:
    def __init__(self, config, dataframes: dict):
        self._config = config
        self._dfs = dataframes

    def search(self, query: str) -> SimpleNamespace:
        """Синхрон дуудалт. main_api нь asyncio.to_thread-ээр thread дотор дуудна."""
        response, context_data = asyncio.run(
            api.local_search(
                config=self._config,
                entities=self._dfs["entities"],
                communities=self._dfs["communities"],
                community_reports=self._dfs["community_reports"],
                text_units=self._dfs["text_units"],
                relationships=self._dfs["relationships"],
                covariates=self._dfs.get("covariates"),
                community_level=COMMUNITY_LEVEL,
                response_type=RESPONSE_TYPE,
                query=query,
            )
        )
        return SimpleNamespace(response=response, context_data=context_data)


def build_local_search_engine() -> LocalSearchEngine:
    settings_path = BACKEND_ROOT / "settings.yaml"
    if not settings_path.exists():
        raise FileNotFoundError(
            f"{settings_path} олдсонгүй. settings.yaml.template-ээс хуулж GraphRAG тохиргоог үүсгэнэ үү."
        )

    config = load_config(root_dir=BACKEND_ROOT)
    dataframes = _resolve_output_files(
        config=config,
        output_list=_OUTPUT_TABLES,
        optional_list=_OPTIONAL_TABLES,
    )
    logger.info(
        "GraphRAG indices loaded: entities=%d, communities=%d, text_units=%d",
        len(dataframes["entities"]), len(dataframes["communities"]), len(dataframes["text_units"]),
    )
    return LocalSearchEngine(config, dataframes)
