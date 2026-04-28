import unittest
import numpy as np
import faiss
import os
import h5py
import pickle
from bioseq_investigator.embeddings import (
    load_embeddings, 
    build_index, 
    get_or_create_index,
    load_embeddings_and_build_index
)
from bioseq_investigator.scoring import get_similarity_score

class TestDistanceMetrics(unittest.TestCase):
    def setUp(self):
        self.test_h5 = "test_embeddings.h5"
        self.test_index = "test_index.index"
        self.test_cache = "test_cache.pkl"
        self.dim = 128
        self.num_entries = 10
        
        # Create a dummy H5 file
        with h5py.File(self.test_h5, 'w') as f:
            for i in range(self.num_entries):
                vec = np.random.random(self.dim).astype(np.float32)
                f.create_dataset(f"acc_{i}", data=vec)

    def tearDown(self):
        for f in [self.test_h5, self.test_index, self.test_cache]:
            if os.path.exists(f):
                os.remove(f)

    def test_load_embeddings(self):
        embeddings, accessions = load_embeddings(self.test_h5)
        self.assertEqual(embeddings.shape, (self.num_entries, self.dim))
        self.assertEqual(len(accessions), self.num_entries)
        self.assertTrue("acc_0" in accessions)

    def test_build_index_persistence(self):
        embeddings, _ = load_embeddings(self.test_h5)
        index = build_index(embeddings, self.test_index)
        self.assertTrue(os.path.exists(self.test_index))
        self.assertEqual(index.ntotal, self.num_entries)
        self.assertEqual(index.metric_type, faiss.METRIC_INNER_PRODUCT)

    def test_get_or_create_index(self):
        # 1. Build and persist
        index, accessions = get_or_create_index(self.test_h5, self.test_index, self.test_cache)
        self.assertTrue(os.path.exists(self.test_index))
        self.assertTrue(os.path.exists(self.test_cache))
        self.assertEqual(len(accessions), self.num_entries)
        
        # 2. Load from persistence (mocking load_embeddings to ensure it's NOT called)
        # However, we can just check if it works
        index2, accessions2 = get_or_create_index(self.test_h5, self.test_index, self.test_cache)
        self.assertEqual(index2.ntotal, self.num_entries)
        self.assertEqual(accessions, accessions2)

    def test_similarity_normalization(self):
        self.assertEqual(get_similarity_score(0.8), 0.8)
        self.assertEqual(get_similarity_score(1.2), 1.0)
        self.assertEqual(get_similarity_score(-0.5), 0.0)

if __name__ == '__main__':
    unittest.main()
