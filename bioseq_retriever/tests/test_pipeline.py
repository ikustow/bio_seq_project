import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import os
from src.search import search_top_k
from src.scoring import rank_sequences
from src.reranking import LocalReranker, _format_record_for_reranking
from src.utils import translate_dna_to_protein, setup_environment
from src.pipeline import run_bioseq_pipeline

class TestBioSeqUtilities(unittest.TestCase):
    def test_dna_translation(self):
        dna = "ATG" * 3 # AT GAT GAT
        protein = translate_dna_to_protein(dna)
        self.assertEqual(protein, "MDD")
        
        dna_with_stop = "ATGTGA"
        protein_stop = translate_dna_to_protein(dna_with_stop)
        self.assertEqual(protein_stop, "M") # Stop codon '*' stops translation

    def test_dna_translation_error(self):
        with self.assertRaises(Exception):
            translate_dna_to_protein("AT") # Not divisible by 3

    @patch.dict(os.environ, {"MISTRAL_API_KEY": "test_key"})
    def test_setup_environment(self):
        self.assertEqual(setup_environment(), "test_key")

class TestPipeline(unittest.TestCase):
    def test_format_record(self):
        record = {
            "primaryAccession": "P12345",
            "organism": {"scientificName": "E. coli"},
            "genes": [{"geneName": {"value": "abcA"}}],
            "proteinDescription": {"recommendedName": {"fullName": {"value": "Super Protein"}}},
            "comments": [{"commentType": "FUNCTION", "note": {"texts": [{"value": "Does things."}]}}]
        }
        fmt = _format_record_for_reranking(record)
        self.assertIn("Gene: abcA", fmt)
        self.assertIn("Organism: E. coli", fmt)
        self.assertIn("Description: Does things.", fmt)
        self.assertNotIn("P12345", fmt) # Accession should be excluded

    @patch('src.pipeline.get_llm')
    @patch('src.pipeline.get_or_create_index')
    @patch('src.pipeline.get_prottrans_embedder')
    @patch('src.pipeline.search_top_k')
    @patch('src.pipeline.get_uniprot_records')
    @patch('src.pipeline.LocalReranker')
    def test_run_bioseq_pipeline_mock(self, mock_reranker, mock_uniprot, mock_search, mock_embedder, mock_index, mock_llm):
        # Setup mocks
        mock_llm_instance = MagicMock()
        mock_llm.return_value = mock_llm_instance
        
        # Mock structured output for extraction
        class MockExtraction:
            sequence_or_path = "MALT"
            input_type = "SEQUENCE"
            context = "test context"
            sequence_type = "PROTEIN"
            is_confident = True
            reasoning = "test reasoning"
            
        mock_llm_instance.with_structured_output.return_value.invoke.return_value = MockExtraction()
        
        mock_index.return_value = (MagicMock(), ["ACC_1"])
        mock_embedder.return_value = (None, None, "cpu")
        mock_search.return_value = [("ACC_1", 0.9)]
        mock_uniprot.return_value = [{"primaryAccession": "ACC_1"}]
        mock_reranker.return_value.rerank_by_context.return_value = [{"primaryAccession": "ACC_1"}]
        
        # Run
        result = run_bioseq_pipeline("dummy prompt")
        
        # Verify
        self.assertEqual(result['sequence'], "MALT")
        self.assertEqual(result['sequence_type'], "PROTEIN")
        self.assertTrue(result['is_confident'])
        self.assertEqual(len(result['final_results']), 1)
        self.assertIsNone(result['error'])

if __name__ == '__main__':
    unittest.main()
