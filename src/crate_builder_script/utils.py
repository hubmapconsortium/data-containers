"""provide small utility calls"""
import hashlib
import requests

def get_sha256_from_url(url: str, chunk_size : int =16384) -> str:
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
