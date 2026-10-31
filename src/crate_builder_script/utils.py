"""Provide small utility calls."""
import hashlib

import requests

FILESET_KEYWORDS = ["id", "name", "contained_in", "includes", "encoding_formats"]

RECORDSET_KEYWORDS = ["id", "name", "description", "fields"]


def safe_id(base: str, key: str | None = None) -> str:
    """Work around mlcommons restrictions on id strings."""
    if key:
        id = f"{base}/{key}"
    else:
        id = f"{base}"
    id = id.replace(" ","--")
    return id


def rel_path_to_id(rel_path: str) -> str:
    """Provide a standard mapping from paths to ID strings."""
    return safe_id(rel_path)


def get_sha256_from_url(url: str, chunk_size: int = 16384) -> str:
    """
    Given a file URL, return the sha256 of the file.

    Thanks to Google Gemini. Small files only, please.
    """
    hash = hashlib.sha256()
    with requests.get(url, stream=True) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if chunk:
                hash.update(chunk)
    return hash.hexdigest()
