"""BGE semantic retrieval, with an explicit offline lexical baseline."""

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from sentinel.config import EMBEDDING_MODEL
from sentinel.nlq.schema import ALIASES, JOINS, METRICS, SCHEMA


class SchemaRetriever:
    def __init__(self, backend="lexical", local_files_only=True):
        self.backend = backend
        self.names = list(SCHEMA)
        self.documents = [
            f"{name} {ALIASES[name]} columns {' '.join(SCHEMA[name])} {' '.join(METRICS.values())}"
            for name in self.names
        ]
        if backend == "bge":
            from sentence_transformers import SentenceTransformer

            self.encoder = SentenceTransformer(EMBEDDING_MODEL, local_files_only=local_files_only)
            self.vectors = self.encoder.encode(self.documents, normalize_embeddings=True)
        elif backend == "lexical":
            self.encoder = TfidfVectorizer(ngram_range=(1, 2))
            self.vectors = self.encoder.fit_transform(self.documents)
        else:
            raise ValueError("Retrieval backend must be lexical or bge")

    def retrieve(self, question, top_k=3):
        if self.backend == "bge":
            query = self.encoder.encode(
                ["Represent this sentence for searching relevant passages: " + question],
                normalize_embeddings=True,
            )
            scores = (self.vectors @ query.T).ravel()
        else:
            scores = (self.vectors @ self.encoder.transform([question]).T).toarray().ravel()
        selected = [self.names[i] for i in np.argsort(-scores)[:top_k]]
        return {
            "backend": self.backend,
            "views": {name: SCHEMA[name] for name in selected},
            "metrics": METRICS,
            "aliases": {name: ALIASES[name] for name in selected},
            "allowed_joins": JOINS,
        }
