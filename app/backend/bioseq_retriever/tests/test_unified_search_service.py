import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
import sys
import os

# Ensure services is in path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
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
