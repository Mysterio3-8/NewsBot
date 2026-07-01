"""Защита от дублей: по external_id и по SimHash текста (раздел 14 SPEC.md)."""
from __future__ import annotations

from simhash import Simhash

SIMHASH_BITS = 64


def compute_simhash(text: str) -> int:
    return Simhash(text, f=SIMHASH_BITS).value


def hamming_distance(hash_a: int, hash_b: int) -> int:
    return bin(hash_a ^ hash_b).count("1")


def similarity(hash_a: int, hash_b: int) -> float:
    return 1.0 - hamming_distance(hash_a, hash_b) / SIMHASH_BITS


def is_duplicate_external_id(external_id: str, existing_external_ids: set[str]) -> bool:
    return external_id in existing_external_ids


def find_similar_hash(
    new_hash: int, existing_hashes: list[int], similarity_threshold: float
) -> int | None:
    """Возвращает первый существующий хэш, похожий на новый выше порога, либо None."""
    for existing_hash in existing_hashes:
        if similarity(new_hash, existing_hash) >= similarity_threshold:
            return existing_hash
    return None
