"""Artefact stores for large tool outputs, files and transcripts (Project 0 SeaweedFS S3)."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


class ArtefactStore(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        """Store ``data``; return its URI (``s3://bucket/key`` or ``memory://key``)."""

    @abstractmethod
    async def get(self, uri: str) -> bytes: ...


class MemoryArtefactStore(ArtefactStore):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        self.objects[key] = data
        return f"memory://{key}"

    async def get(self, uri: str) -> bytes:
        return self.objects[uri.removeprefix("memory://")]


class S3ArtefactStore(ArtefactStore):
    """Any S3-compatible store; SeaweedFS from Project 0 by default. Credentials come from secrets."""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def ensure_bucket(self) -> str:
        try:
            self._s3.head_bucket(Bucket=self.bucket)
            return "exists"
        except ClientError:
            self._s3.create_bucket(Bucket=self.bucket)
            return "created"

    async def put(self, key: str, data: bytes, content_type: str = "application/json") -> str:
        await asyncio.to_thread(
            self._s3.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return f"s3://{self.bucket}/{key}"

    async def get(self, uri: str) -> bytes:
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        obj = await asyncio.to_thread(self._s3.get_object, Bucket=bucket, Key=key)
        return bytes(obj["Body"].read())
