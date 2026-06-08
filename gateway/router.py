"""LLM Gateway with multi-provider routing."""
import asyncio
import hashlib
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import redis.asyncio as aioredis
from sentence_transformers import SentenceTransformer
import numpy as np

class LLMRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.7
    preferred_provider: Optional[str] = None
    quality_tier: str = "standard"  # standard | premium | economy

PROVIDER_COSTS = {  # per 1K tokens (input/output)
    "gemini-1.5-flash": (0.00035, 0.00105),
    "gpt-4o-mini": (0.00015, 0.0006),
    "claude-3-haiku": (0.00025, 0.00125),
    "mistral-7b": (0.00025, 0.00025),
    "gpt-4o": (0.005, 0.015),
    "gemini-1.5-pro": (0.00125, 0.005),
}

app = FastAPI(title="LLM Gateway")

class SemanticCache:
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = None
        self.encoder = SentenceTransformer("all-MiniLM-L6-v2")
        self.redis_url = redis_url
        self.similarity_threshold = 0.95

    async def get_similar(self, prompt: str) -> Optional[str]:
        if not self.redis: self.redis = await aioredis.from_url(self.redis_url)
        query_emb = self.encoder.encode(prompt)
        keys = await self.redis.keys("cache:emb:*")
        for key in keys[:100]:  # check up to 100 cached entries
            stored = await self.redis.get(key)
            if stored:
                stored_emb = np.frombuffer(stored, dtype=np.float32)
                sim = float(np.dot(query_emb, stored_emb) / (np.linalg.norm(query_emb) * np.linalg.norm(stored_emb)))
                if sim >= self.similarity_threshold:
                    cache_key = key.decode().replace("cache:emb:", "cache:resp:")
                    resp = await self.redis.get(cache_key)
                    if resp: return resp.decode()
        return None

    async def store(self, prompt: str, response: str):
        if not self.redis: self.redis = await aioredis.from_url(self.redis_url)
        h = hashlib.md5(prompt.encode()).hexdigest()
        emb = self.encoder.encode(prompt).astype(np.float32)
        await self.redis.set(f"cache:emb:{h}", emb.tobytes(), ex=86400)
        await self.redis.set(f"cache:resp:{h}", response, ex=86400)
