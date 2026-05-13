import unittest
import re
from src.utils import clean_sequence, translate_dna_to_protein

class TestUtils(unittest.TestCase):
    def test_clean_sequence(self):
        self.assertEqual(clean_sequence(">header\nATGC\nATGC"), "ATGCATGC")
        self.assertEqual(clean_sequence("  A T G C  "), "ATGC")
        self.assertEqual(clean_sequence("ATG123!"), "ATG")

    def test_translate_dna(self):
        self.assertEqual(translate_dna_to_protein("ATG"), "M")
        with self.assertRaises(Exception):
            translate_dna_to_protein("AT")

if __name__ == '__main__':
    unittest.main()
