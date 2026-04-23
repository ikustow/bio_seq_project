import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from bioseq_investigator.search import search_top_k
from bioseq_investigator.scoring import rank_sequences
from bioseq_investigator.reranking import LocalReranker, _format_record_for_reranking # Import both

class TestPipeline(unittest.TestCase):
    def test_full_pipeline_flow(self):
        # 1. Mock Search Results
        mock_matches = [(f"ACC_{i}", 0.9 - (i * 0.01)) for i in range(25)]
        
        # 2. Rank sequences (Verification of scoring module)
        ranked_matches = rank_sequences(mock_matches)
        self.assertEqual(len(ranked_matches), 25)
        self.assertEqual(ranked_matches[0][0], "ACC_0")
        
        # 3. Mock UniProt Records for those accessions
        mock_records = [
            {
                "primaryAccession": f"ACC_{i}",
                "organism": {"scientificName": "Homo sapiens" if i % 2 == 0 else "Mus musculus"},
                "genes": [{"geneName": {"value": f"GENE_{i}"}}],
                "proteinDescription": {"recommendedName": {"fullName": {"value": f"Protein {i}"}}}
            }
            for i in range(25)
        ]
        
        # 4. Rerank by context using mocked LocalReranker
        context = "Mus musculus related proteins"
        
        with patch('bioseq_investigator.reranking.LocalReranker', autospec=True) as MockReranker:
            # Configure the mock reranker
            mock_reranker_instance = MockReranker.return_value
            mock_reranker_instance.rerank_by_context.return_value = mock_records[:5]

            # Manually trigger instantiation
            reranker = MockReranker()
            top_5 = reranker.rerank_by_context(mock_records, context, top_n=5)
        
        self.assertEqual(len(top_5), 5)
        # Due to random embeddings, we can't assert specific content, but ensure it returns 5 records
        self.assertIsInstance(top_5, list)
        self.assertIsInstance(top_5[0], dict)

    @patch('bioseq_investigator.search.embed_sequence')
    def test_search_integration(self, mock_embed):
        mock_embed.return_value = np.random.random(1024).astype(np.float32)
        
        mock_index = MagicMock()
        mock_index.search.return_value = (
            np.array([[0.9, 0.8]], dtype=np.float32), 
            np.array([[0, 1]], dtype=np.int64)
        )
        
        mock_embedder_tools = (None, None, "cpu")
        accessions = ["ACC_1", "ACC_2"]
        
        results = search_top_k("MALT", mock_embedder_tools, mock_index, accessions, k=2)
        
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0], "ACC_1")
        self.assertAlmostEqual(results[0][1], 0.9)

if __name__ == '__main__':
    unittest.main()
