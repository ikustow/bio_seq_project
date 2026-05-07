from typing import List, Dict, Any
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
import os

class RerankedDocument(BaseModel):
    index: int = Field(description="The index of the document in the original list.")
    score: float = Field(description="The relevance score of the document.")

class RerankOutput(BaseModel):
    rankings: List[RerankedDocument] = Field(description="The re-ranked list of document indices.")

class LLMReranker:
    def __init__(self, model_name: str = "gpt-4o-mini"):
        self.llm = ChatOpenAI(model=model_name, temperature=0)
        self.structured_llm = self.llm.with_structured_output(RerankOutput)

    def _format_docs(self, records: List[Dict[str, Any]]) -> str:
        formatted = ""
        for i, rec in enumerate(records):
            # Assumes records follow the structure produced by the graph_core enrichment
            protein = rec.get('name', 'N/A')
            desc = rec.get('description', 'N/A')
            formatted += f"[{i}] Protein: {protein}, Description: {desc}\n"
        return formatted

    def rerank(self, records: List[Dict[str, Any]], query: str, top_n: int = 5) -> List[Dict[str, Any]]:
        if not records: return []
        
        doc_str = self._format_docs(records)
        prompt = (
            f"Query: {query}\n\n"
            f"Evaluate relevance for these proteins:\n{doc_str}\n\n"
            "Output ranked indices."
        )
        
        result = self.structured_llm.invoke(prompt)
        indices = [r.index for r in result.rankings]
        return [records[i] for i in indices if i < len(records)][:top_n]
