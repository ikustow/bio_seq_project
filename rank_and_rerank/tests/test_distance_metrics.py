import unittest
import numpy as np
import faiss
from bioseq_investigator.embeddings import load_embeddings_and_build_index
from bioseq_investigator.scoring import get_similarity_score
import os
import h5py

class TestDistanceMetrics(unittest.TestCase):
    def setUp(self):
        self.test_h5 = "test_embeddings.h5"
        self.dim = 128
        self.num_entries = 10
        
        # Create a dummy H5 file
        with h5py.File(self.test_h5, 'w') as f:
            for i in range(self.num_entries):
                # Creating normalized-ish vectors
                vec = np.random.random(self.dim).astype(np.float32)
                f.create_dataset(f"acc_{i}", data=vec)

    def tearDown(self):
        if os.path.exists(self.test_h5):
            os.remove(self.test_h5)

    def test_cosine_similarity_index(self):
        index, accessions = load_embeddings_and_build_index(self.test_h5)
        
        # Check if metric is METRIC_INNER_PRODUCT
        self.assertEqual(index.metric_type, faiss.METRIC_INNER_PRODUCT)
        
        # Check if we can search
        query = np.random.random((1, self.dim)).astype(np.float32)
        faiss.normalize_L2(query)
        distances, indices = index.search(query, 5)
        
        self.assertEqual(len(distances[0]), 5)
        # Cosine similarity for normalized vectors should be between -1 and 1
        # (mostly 0-1 for random positive vectors)
        for dist in distances[0]:
            self.assertGreaterEqual(dist, -1.0001)
            self.assertLessEqual(dist, 1.0001)

    def test_similarity_normalization(self):
        self.assertEqual(get_similarity_score(0.8), 0.8)
        self.assertEqual(get_similarity_score(1.2), 1.0)
        self.assertEqual(get_similarity_score(-0.5), 0.0)

if __name__ == '__main__':
    unittest.main()
