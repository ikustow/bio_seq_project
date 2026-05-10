"""Runtime switches for the Streamlit frontend."""

# When True, every chat prompt is sent through ``chat_pipeline.run_turn``
# (live embeddings retriever + chat-LLM follow-up). When False, the UI
# falls back to the scripted-demo flow in ``mock.conversation``.
USE_VECTOR_DB_MODE: bool = True
