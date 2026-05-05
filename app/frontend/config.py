"""Runtime switches for the Streamlit frontend."""

# When True, every chat prompt is sent to the graph/vector DB retriever via
# app/backend/agents_core/retriever_agent/pipeline_interface.py.
USE_VECTOR_DB_MODE: bool = True
