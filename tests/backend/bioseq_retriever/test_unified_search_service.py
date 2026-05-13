import pytest
import numpy as np
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

BIOSEQ_RETRIEVER_ROOT = Path(__file__).resolve().parents[3] / "app" / "backend" / "bioseq_retriever"
if str(BIOSEQ_RETRIEVER_ROOT) not in sys.path:
    sys.path.insert(0, str(BIOSEQ_RETRIEVER_ROOT))

from services.search_service import app

@pytest.fixture
def client():
    with patch('services.search_service.get_or_create_index') as mock_idx:
        mock_idx.return_value = (MagicMock(), ["P1"])
        return TestClient(app)

def test_search_endpoint(client):
    with patch('services.search_service.index') as mock_index:
        mock_index.search.return_value = (np.array([[0.9]]), np.array([[0]]))
        
        response = client.post("/search", json={"sequence": "MALW", "k": 1})
        assert response.status_code == 200
        assert response.json()["results"][0]["accession"] == "P1"
