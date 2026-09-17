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
import os
from pathlib import Path
from types import SimpleNamespace

import litellm
import graphrag.api as api
from graphrag.cli.query import _resolve_output_files
from graphrag.config.load_config import load_config

logger = logging.getLogger("talent_ai.graphrag")

_MODULE_DIR = Path(__file__).resolve().parent

# GRAPHRAG_ROOT (.env-д тодорхойлогддог) индексийн (settings.yaml + output/)
# байрлалыг backend модулийн байрлалаас өөр газар зааж болохын тулд байдаг.
# Урьд нь энэ хувьсагчийг хэзээ ч уншдаггүй байсан (үргэлж модулийн
# директорыг ашигладаг байсан) — .env.example-д "тохируулах ёстой мэт" харагдаад
# бодит байдал дээр ямар ч нөлөөгүй байсан тул хэрэглэгчийг төөрөгдүүлдэг байв.
#
# Анхаарах зүйл: relative зам (жишээ нь .env.example-ийн анхны утга ".") -ийг
# process-ийн CWD-тэй холбож ТАЙЛБАРЛАЖ БОЛОХГҮЙ — сервер өөр CWD-с (жишээ нь
# repo root) асаагдвал энэ нь буруу директор руу заагаад, indexийг
# чимээгүйхэн олохгүй болгож, яг засварлаж буй "index bypass" алдааг өөр
# хэлбэрээр дахин үүсгэнэ. Тиймээс relative замыг үргэлж энэ модулийн
# байрлалаас (_MODULE_DIR) харьцангуй гэж үзнэ, CWD-с биш.
_raw_graphrag_root = os.environ.get("GRAPHRAG_ROOT", "").strip()
if _raw_graphrag_root:
    _root_path = Path(_raw_graphrag_root).expanduser()
    BACKEND_ROOT = (_root_path if _root_path.is_absolute() else _MODULE_DIR / _root_path).resolve()
else:
    BACKEND_ROOT = _MODULE_DIR
COMMUNITY_LEVEL = 2
RESPONSE_TYPE = "Multiple Paragraphs"

# Context-window token budget (max_context_tokens, community_prop,
# text_unit_prop, top_k_entities, top_k_relationships) is NOT tuned here —
# it lives in settings.yaml's local_search: block, since graphrag reads
# those straight from GraphRagConfig.local_search via
# query/factory.py:get_local_search_engine(). COMMUNITY_LEVEL/RESPONSE_TYPE
# above only pick which community tier and how verbose the synthesized
# answer is; they don't bound how many context tokens go into producing it.

_OUTPUT_TABLES = ["communities", "community_reports", "text_units", "relationships", "entities"]
_OPTIONAL_TABLES = ["covariates"]


def _fill_missing_embedding_index(response):
    try:
        for i, item in enumerate(response.data):
            if item.get("index") is None:
                item["index"] = i
    except Exception:
        pass
    return response


def _patch_litellm_embedding_index_bug() -> None:
    """Gemini-ийн OpenAI-нийцтэй embeddings endpoint нь хариу дахь
    `data[i].index`-г бөглөдөггүй (null буцаадаг). graphrag_llm сан үүнийг
    заавал int байх ёстой гэж pydantic-аар шалгадаг тул
    "Input should be a valid integer" алдаа өгдөг.

    graphrag.api.local_search бол ASYNC бөгөөд query-ийн embedding-ийг
    graphrag_llm.embedding.lite_llm_embedding._base_embedding_async()-ээр
    дуудаж, энэ нь litellm.aembedding()-г ашигладаг — litellm.embedding()
    (sync) биш. Зөвхөн sync хувилбарыг patch хийвэл query-ийн embedding
    дуудалт бүр pydantic ValidationError шидээд, main_api.py-ийн
    agent_psychometric_expert() дахь try/except үүнийг чимээгүй барьж,
    GraphRAG индекс ХЭЗЭЭ Ч ашиглагдахгүйгээр Gemini түүхий мэдлэгээрээ
    хариулдаг болдог (индекс алгасагдана). Тиймээс sync БОЛОН async
    аль алиныг нь patch хийнэ.
    """
    if not getattr(litellm.embedding, "_talent_ai_index_patched", False):
        original_embedding = litellm.embedding

        def patched_embedding(*args, **kwargs):
            return _fill_missing_embedding_index(original_embedding(*args, **kwargs))

        patched_embedding._talent_ai_index_patched = True
        litellm.embedding = patched_embedding

    if not getattr(litellm.aembedding, "_talent_ai_index_patched", False):
        original_aembedding = litellm.aembedding

        async def patched_aembedding(*args, **kwargs):
            return _fill_missing_embedding_index(await original_aembedding(*args, **kwargs))

        patched_aembedding._talent_ai_index_patched = True
        litellm.aembedding = patched_aembedding


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
    logger.info("GraphRAG root dir resolved to: %s", BACKEND_ROOT)

    settings_path = BACKEND_ROOT / "settings.yaml"
    if not settings_path.exists():
        raise FileNotFoundError(
            f"{settings_path} олдсонгүй. settings.yaml.template-ээс хуулж GraphRAG тохиргоог үүсгэнэ үү."
        )

    output_dir = BACKEND_ROOT / "output"
    if not output_dir.is_dir() or not any(output_dir.glob("*.parquet")):
        raise FileNotFoundError(
            f"{output_dir} дотор индексийн parquet файл олдсонгүй. "
            "GraphRAG индексийг эхлээд `graphrag index` командаар үүсгэнэ үү."
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
