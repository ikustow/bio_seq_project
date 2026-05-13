import unittest
from unittest.mock import patch, MagicMock
from src.search import search_top_k

class TestSearchClient(unittest.TestCase):
    @patch('src.search.default_api_client')
    def test_search_top_k(self, mock_client):
        # Mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": [{"accession": "P1", "score": 0.9}]}
        mock_client.request_with_retry.return_value = mock_response
        
        results = search_top_k("MALW", k=1)
        
        # Verify call
        mock_client.request_with_retry.assert_called()
        self.assertEqual(results, [("P1", 0.9)])

if __name__ == '__main__':
    unittest.main()
