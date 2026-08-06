"""
VectorMemory — ChromaDB integration for semantic search.

Stores:
- Conversations (messages between agent and clients)
- Lead context (profile, stage, interests)
- Knowledge base (KP, presentations, cases)
"""
import logging
from pathlib import Path
from typing import Optional
import chromadb
from chromadb.config import Settings

logger = logging.getLogger("agent.memory")

# Storage path
CHROMA_PATH = Path(__file__).parent.parent.parent / "data" / "chromadb"


class VectorMemory:
    """Vector memory using ChromaDB for semantic search."""

    def __init__(self, persist_dir: str | None = None):
        self.persist_dir = str(persist_dir or CHROMA_PATH)
        self._client: Optional[chromadb.ClientAPI] = None
        self._collections: dict[str, chromadb.Collection] = {}

    @property
    def client(self) -> chromadb.ClientAPI:
        if self._client is None:
            Path(self.persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info("ChromaDB initialized at %s", self.persist_dir)
        return self._client

    def _get_collection(self, name: str) -> chromadb.Collection:
        if name not in self._collections:
            self._collections[name] = self.client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    # ---- Conversations ----

    def save_message(self, lead_id: int, role: str, content: str, metadata: dict = None):
        """Save a conversation message."""
        col = self._get_collection("conversations")
        doc_id = f"lead_{lead_id}_{col.count()}"
        meta = {
            "lead_id": lead_id,
            "role": role,  # "user" or "assistant"
            "timestamp": self._now(),
        }
        if metadata:
            meta.update(metadata)

        col.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[meta],
        )
        logger.debug("Saved message for lead %d: %s", lead_id, content[:50])

    def search_conversations(self, lead_id: int, query: str, n_results: int = 5) -> list[dict]:
        """Search conversation history for a lead."""
        col = self._get_collection("conversations")
        if col.count() == 0:
            return []

        results = col.query(
            query_texts=[query],
            n_results=n_results,
            where={"lead_id": lead_id},
        )

        messages = []
        for i, doc in enumerate(results["documents"][0]):
            messages.append({
                "content": doc,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })
        return messages

    def get_recent_messages(self, lead_id: int, limit: int = 20) -> list[dict]:
        """Get recent messages for a lead (chronological)."""
        col = self._get_collection("conversations")
        if col.count() == 0:
            return []

        results = col.get(
            where={"lead_id": lead_id},
            limit=limit,
        )

        messages = []
        for i, doc in enumerate(results["documents"]):
            messages.append({
                "content": doc,
                "metadata": results["metadatas"][i] if results["metadatas"] else {},
            })

        # Sort by timestamp
        messages.sort(key=lambda x: x["metadata"].get("timestamp", ""))
        return messages

    # ---- Lead Context ----

    def save_lead_context(self, lead_id: int, context: dict):
        """Save lead context (profile, stage, interests)."""
        col = self._get_collection("lead_context")
        doc = self._format_lead_context(context)

        col.upsert(
            ids=[f"lead_{lead_id}"],
            documents=[doc],
            metadatas=[{
                "lead_id": lead_id,
                "stage": context.get("stage", "new"),
                "industry": context.get("industry", ""),
                "company": context.get("company", ""),
            }],
        )

    def get_lead_context(self, lead_id: int) -> dict | None:
        """Get lead context."""
        col = self._get_collection("lead_context")
        try:
            result = col.get(ids=[f"lead_{lead_id}"])
            if result["documents"]:
                return {
                    "document": result["documents"][0],
                    "metadata": result["metadatas"][0] if result["metadatas"] else {},
                }
        except Exception:
            pass
        return None

    def search_leads_by_context(self, query: str, n_results: int = 10) -> list[dict]:
        """Search leads by semantic similarity."""
        col = self._get_collection("lead_context")
        if col.count() == 0:
            return []

        results = col.query(
            query_texts=[query],
            n_results=n_results,
        )

        leads = []
        for i, doc in enumerate(results["documents"][0]):
            leads.append({
                "document": doc,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })
        return leads

    # ---- Knowledge Base (KP, Presentations) ----

    def save_knowledge(self, doc_id: str, content: str, doc_type: str, industry: str, metadata: dict = None):
        """Save knowledge document (KP, presentation, case)."""
        col = self._get_collection("knowledge_base")
        meta = {
            "doc_type": doc_type,  # "kp", "presentation", "case"
            "industry": industry,
            "timestamp": self._now(),
        }
        if metadata:
            meta.update(metadata)

        col.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[meta],
        )
        logger.info("Saved knowledge: %s (%s, %s)", doc_id, doc_type, industry)

    def search_knowledge(self, query: str, doc_type: str = None, industry: str = None, n_results: int = 5) -> list[dict]:
        """Search knowledge base."""
        col = self._get_collection("knowledge_base")
        if col.count() == 0:
            return []

        where = {}
        if doc_type:
            where["doc_type"] = doc_type
        if industry:
            where["industry"] = industry

        if len(where) > 1:
            where = {"$and": [{k: v} for k, v in where.items()]}
        elif not where:
            where = None

        results = col.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
        )

        docs = []
        for i, doc in enumerate(results["documents"][0]):
            docs.append({
                "id": results["ids"][0][i],
                "content": doc,
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })
        return docs

    def get_knowledge_by_industry(self, industry: str, doc_type: str = None) -> list[dict]:
        """Get all knowledge documents for an industry."""
        col = self._get_collection("knowledge_base")
        if doc_type:
            where = {"$and": [{"industry": industry}, {"doc_type": doc_type}]}
        else:
            where = {"industry": industry}

        results = col.get(where=where)

        docs = []
        for i, doc in enumerate(results["documents"]):
            docs.append({
                "id": results["ids"][i],
                "content": doc,
                "metadata": results["metadatas"][i] if results["metadatas"] else {},
            })
        return docs

    def delete_knowledge(self, doc_id: str):
        """Delete a knowledge document."""
        col = self._get_collection("knowledge_base")
        try:
            col.delete(ids=[doc_id])
            logger.info("Deleted knowledge: %s", doc_id)
        except Exception as e:
            logger.error("Failed to delete knowledge %s: %s", doc_id, e)

    # ---- Utilities ----

    def _format_lead_context(self, context: dict) -> str:
        """Format lead context as text for embedding."""
        parts = []
        if context.get("company"):
            parts.append(f"Компания: {context['company']}")
        if context.get("industry"):
            parts.append(f"Отрасль: {context['industry']}")
        if context.get("stage"):
            parts.append(f"Стадия: {context['stage']}")
        if context.get("needs"):
            parts.append(f"Потребности: {context['needs']}")
        if context.get("objections"):
            parts.append(f"Возражения: {context['objections']}")
        if context.get("notes"):
            parts.append(f"Заметки: {context['notes']}")
        return "\n".join(parts) if parts else "Нет данных"

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().isoformat()

    def stats(self) -> dict:
        """Get collection statistics."""
        return {
            "conversations": self._get_collection("conversations").count(),
            "lead_context": self._get_collection("lead_context").count(),
            "knowledge_base": self._get_collection("knowledge_base").count(),
        }