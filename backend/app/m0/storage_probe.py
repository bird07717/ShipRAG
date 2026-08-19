from __future__ import annotations

import json
from datetime import timedelta
from io import BytesIO
from urllib.parse import quote
from uuid import uuid4

import httpx
from minio import Minio

from app.core.config import get_settings
from app.m0.fixtures import make_text_image


def main() -> None:
    settings = get_settings()
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_secure,
    )
    bucket = settings.minio_image_bucket
    object_name = f"m0-probe/{uuid4()}.png"
    image = make_text_image()
    client.put_object(bucket, object_name, BytesIO(image), len(image), content_type="image/png")
    try:
        scheme = "https" if settings.minio_secure else "http"
        anonymous_url = (
            f"{scheme}://{settings.minio_endpoint}/"
            f"{quote(bucket, safe='')}/{quote(object_name, safe='/')}"
        )
        anonymous_response = httpx.get(anonymous_url, timeout=5, follow_redirects=False)
        private = anonymous_response.status_code in {401, 403}
        signed_url = client.presigned_get_object(bucket, object_name, expires=timedelta(minutes=1))
        response = httpx.get(signed_url, timeout=5, follow_redirects=False)
        response.raise_for_status()
        report = {
            "status": "passed" if private and response.content == image else "failed",
            "bucket_private": private,
            "anonymous_fetch_status": anonymous_response.status_code,
            "signed_url_fetch": response.status_code == 200,
            "content_matches": response.content == image,
            "external_strategy": "base64_data_url",
            "signed_url_disclosed": False,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        client.remove_object(bucket, object_name)


if __name__ == "__main__":
    main()
