from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings

from config.ai_conf import EMBEDDING_DEVICE, EMBEDDING_MODEL_NAME


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": EMBEDDING_DEVICE},
        encode_kwargs={"normalize_embeddings": True},
    )
