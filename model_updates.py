"""Report-only check for newer upstream model files.

Photo Cull always scores with the pinned revisions in `model_config`. This module only
reports that upstream has moved on. It needs `requests` and nothing from the
machine-learning stack, so a cache-only run can use it.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import requests

from model_config import (
    AESTHETIC_MODEL_REVISION,
    AESTHETIC_WEIGHTS_NAME,
    CLIP_MODEL_ID,
    CLIP_MODEL_REVISION,
    MUSIQ_MODEL_REVISION,
    MUSIQ_WEIGHTS_SHA256,
    MUSIQ_WEIGHTS_URL,
)

REQUEST_TIMEOUT = (3.05, 5.0)
HEADERS = {"User-Agent": "photo-cull-model-update-check"}
CLIP_REVISION_URL = f"https://huggingface.co/api/models/{CLIP_MODEL_ID}/revision/main"
MUSIQ_LATEST_URL = MUSIQ_WEIGHTS_URL.replace(MUSIQ_MODEL_REVISION, "main")
AESTHETIC_COMMITS_URL = (
    "https://api.github.com/repos/christophschuhmann/improved-aesthetic-predictor/commits"
)
LOOKUP_ERRORS = (requests.RequestException, KeyError, IndexError, TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class ModelUpdate:
    name: str
    pinned: str
    latest: str


@dataclass(frozen=True, slots=True)
class ModelUpdateCheck:
    updates: tuple[ModelUpdate, ...]
    unavailable: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        if self.updates:
            status = "updates_available"
        elif self.unavailable:
            status = "incomplete"
        else:
            status = "current"
        return {
            "status": status,
            "updates": [
                {"model": update.name, "pinned": update.pinned, "latest": update.latest}
                for update in self.updates
            ],
            "unavailable": dict(self.unavailable),
        }


def _clip_revisions() -> tuple[str, str]:
    response = requests.get(CLIP_REVISION_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return CLIP_MODEL_REVISION, str(response.json()["sha"])


def _musiq_hashes() -> tuple[str, str]:
    """Compare file content hashes: the weights repo also holds unrelated models."""
    response = requests.head(
        MUSIQ_LATEST_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=False
    )
    response.raise_for_status()
    return MUSIQ_WEIGHTS_SHA256, response.headers["X-Linked-ETag"].strip('"')


def _aesthetic_commits() -> tuple[str, str]:
    """Compare the last commit that touched the file at the pin and at the default branch.

    The repository gets unrelated commits, so comparing branch heads would report a
    change on every run.
    """

    def last_commit(ref: str | None) -> str:
        params = {"path": AESTHETIC_WEIGHTS_NAME, "per_page": 1}
        if ref is not None:
            params["sha"] = ref
        response = requests.get(
            AESTHETIC_COMMITS_URL, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return str(response.json()[0]["sha"])

    return last_commit(AESTHETIC_MODEL_REVISION), last_commit(None)


LOOKUPS: dict[str, Callable[[], tuple[str, str]]] = {
    "CLIP": _clip_revisions,
    "MUSIQ": _musiq_hashes,
    "aesthetic head": _aesthetic_commits,
}


def check_model_updates() -> ModelUpdateCheck:
    """Compare each pinned model with upstream. Never raises for a lookup failure."""
    with ThreadPoolExecutor(max_workers=len(LOOKUPS)) as pool:
        futures = {name: pool.submit(lookup) for name, lookup in LOOKUPS.items()}
    updates: list[ModelUpdate] = []
    unavailable: dict[str, str] = {}
    for name, future in futures.items():
        try:
            pinned, latest = future.result()
        except LOOKUP_ERRORS as error:
            unavailable[name] = f"{type(error).__name__}: {error}"
            continue
        if pinned != latest:
            updates.append(ModelUpdate(name, pinned, latest))
    return ModelUpdateCheck(tuple(updates), unavailable)
