from datasketch import MinHash
import mmh3

class GraphAnchorIndexer:
    def __init__(self, num_perm=128, k=3):
        self.num_perm = num_perm
        self.k = k

    def compute_signature(self, sequence: str) -> list:
        m = MinHash(num_perm=self.num_perm)
        sequence = sequence.upper()
        for i in range(len(sequence) - self.k + 1):
            kmer = sequence[i:i+self.k].encode('utf-8')
            m.update(kmer)
        return m.digest().tolist()
