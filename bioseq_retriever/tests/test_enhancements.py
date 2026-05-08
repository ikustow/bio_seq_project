import unittest
import os
import json
import shutil
from src.utils import translate_dna_to_protein, get_first_fasta_entry
from src.pipeline import is_secure_path
from src.config import ALLOWED_DATA_DIR

class TestBioSeqEnhancements(unittest.TestCase):
    def setUp(self):
        # Create a dummy FASTA file for testing
        self.test_dir = "data_test_tmp"
        os.makedirs(self.test_dir, exist_ok=True)
        self.fasta_path = os.path.join(self.test_dir, "test.fasta")
        with open(self.fasta_path, "w") as f:
            f.write(">test_seq\nATGC\n")

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_dna_translation(self):
        self.assertEqual(translate_dna_to_protein("ATG"), "M")
        self.assertEqual(translate_dna_to_protein("ATGTGA"), "M")

    def test_pyfaidx_integration(self):
        # This will test if pyfaidx is working
        entry = get_first_fasta_entry(self.fasta_path)
        self.assertIn(">test_seq", entry)
        self.assertIn("ATGC", entry)

    def test_secure_path_validation(self):
        # Mocking ALLOWED_DATA_DIR for this test is tricky due to global import
        # But we can test with the actual value
        import src.pipeline
        original_dir = src.pipeline.ALLOWED_DATA_DIR
        src.pipeline.ALLOWED_DATA_DIR = "data"
        
        self.assertTrue(is_secure_path("data/test.fasta"))
        self.assertTrue(is_secure_path("./data/test.fasta"))
        self.assertFalse(is_secure_path("/etc/passwd"))
        self.assertFalse(is_secure_path("test.fasta")) # outside data/
        self.assertFalse(is_secure_path("data/../../etc/passwd"))
        
        src.pipeline.ALLOWED_DATA_DIR = original_dir

if __name__ == "__main__":
    unittest.main()
