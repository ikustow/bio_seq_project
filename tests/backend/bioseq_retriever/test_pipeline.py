import unittest
from unittest.mock import MagicMock, patch
import os
import sys
from pathlib import Path

BIOSEQ_RETRIEVER_ROOT = Path(__file__).resolve().parents[3] / "app" / "backend" / "bioseq_retriever"
if str(BIOSEQ_RETRIEVER_ROOT) not in sys.path:
    sys.path.insert(0, str(BIOSEQ_RETRIEVER_ROOT))

from src.pipeline import run_bioseq_pipeline, is_secure_path
from src.config import ALLOWED_DATA_DIR

class TestBioSeqUtilities(unittest.TestCase):
    def test_secure_path_validation(self):
        self.assertTrue(is_secure_path(os.path.join(ALLOWED_DATA_DIR, "test.fasta")))
        self.assertFalse(is_secure_path("/etc/passwd"))
        self.assertFalse(is_secure_path("non_allowed_dir/test.fasta"))

class TestPipeline(unittest.TestCase):
    @patch('src.pipeline.get_llm')
    @patch('src.pipeline.search_top_k')
    @patch('src.pipeline.get_uniprot_records')
    @patch('src.pipeline.LocalReranker')
    def test_run_bioseq_pipeline_mock(self, mock_reranker, mock_uniprot, mock_search, mock_llm):
        # Setup mocks
        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        
        class MockExtraction:
            sequence_or_path = "MALT"
            input_type = "SEQUENCE"
            context = "test context"
            sequence_type = "PROTEIN"
            is_confident = True
            reasoning = "test reasoning"
            
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = MockExtraction()
        
        # New unified service call mock
        mock_search.return_value = [("ACC_1", 0.9)]
        mock_uniprot.return_value = [{"primaryAccession": "ACC_1"}]
        mock_reranker.return_value.rerank_by_context.return_value = [{"primaryAccession": "ACC_1"}]
        
        # Use a sync runner wrapper for the test
        import asyncio
        result = asyncio.run(run_bioseq_pipeline("dummy prompt"))
        
        self.assertEqual(result['sequence'], "MALT")
        self.assertEqual(result['sequence_type'], "PROTEIN")
        self.assertTrue(result['is_confident'])
        self.assertEqual(len(result['final_results']), 1)
        self.assertIsNone(result['error'])

if __name__ == '__main__':
    unittest.main()
