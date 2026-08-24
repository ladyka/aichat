#!/usr/bin/env python3
import os
import json
import urllib.request
import urllib.error

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

REQUIRED_KEYS = [
    "S3_ENDPOINT",
    "S3_REGION",
    "S3_BUCKET",
    "S3_PUBLIC_BASE_URL",
    "S3_TENANT_ID",
    "S3_SA_KEY_ID",
    "S3_SA_KEY_SECRET",
]


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'\"")
    return env


def mask(value):
    if not value:
        return "<empty>"
    return value[:6] + "..." + value[-4:] if len(value) > 12 else "***"


def main():
    if not os.path.exists(ENV_PATH):
        print(f"FAIL: .env not found: {ENV_PATH}")
        return 1

    env = load_env(ENV_PATH)
    missing = [k for k in REQUIRED_KEYS if not env.get(k)]
    if missing:
        print(f"FAIL: missing vars in .env: {', '.join(missing)}")
        return 1

    endpoint = env["S3_ENDPOINT"]
    region = env["S3_REGION"]
    bucket = env["S3_BUCKET"]
    public_base = env["S3_PUBLIC_BASE_URL"].rstrip("/")
    tenant_id = env["S3_TENANT_ID"]
    key_id = env["S3_SA_KEY_ID"]
    secret = env["S3_SA_KEY_SECRET"]
    path_style = env.get("S3_PATH_STYLE", "").lower() in ("1", "true", "yes")

    if ":" not in key_id:
        key_id = f"{tenant_id}:{key_id}"

    print("=== S3 Diagnostics (boto3) ===")
    print(f"Endpoint: {endpoint}")
    print(f"Region:   {region}")
    print(f"Bucket:   {bucket}")
    print(f"Key ID:   {mask(key_id)}")
    print(f"Secret:   {mask(secret)}")
    print(f"PathStyle:{path_style}")

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name=region,
        config=Config(
            s3={"addressing_style": "path" if path_style else "virtual"},
            retries={"max_attempts": 2},
            connect_timeout=10,
            read_timeout=15,
        ),
    )

    results = {}

    def step(name, fn):
        print()
        print(f"{name}:")
        try:
            results[name] = ("OK", fn())
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "?")
            msg = e.response.get("Error", {}).get("Message", str(e))
            results[name] = ("FAIL", f"[{code}] {msg}")
            print(f"   FAIL — [{code}] {msg}")
        except Exception as e:
            results[name] = ("FAIL", str(e))
            print(f"   FAIL — {e}")

    def list_buckets():
        resp = s3.list_buckets()
        names = [b["Name"] for b in resp["Buckets"]]
        print(f"   OK — {len(names)} buckets: {names}")
        if bucket not in names:
            print(f"   NOTE — бакет '{bucket}' не виден (нормально для SA с правами только на один бакет)")

    def list_objects():
        resp = s3.list_objects_v2(Bucket=bucket, MaxKeys=5)
        keys = [o["Key"] for o in resp.get("Contents", [])]
        print(f"   OK — {len(keys)} objects: {keys}")

    test_key = "diag-test.txt"
    test_body = f"diag-{os.getpid()}".encode()

    def put_object():
        s3.put_object(Bucket=bucket, Key=test_key, Body=test_body)
        print(f"   OK — uploaded '{test_key}'")

    def get_object():
        body = s3.get_object(Bucket=bucket, Key=test_key)["Body"].read()
        print(f"   OK — content: '{body.decode()}'")
        assert body == test_body, "content mismatch"

    def delete_object():
        s3.delete_object(Bucket=bucket, Key=test_key)
        print(f"   OK — deleted '{test_key}'")

    def get_policy():
        resp = s3.get_bucket_policy(Bucket=bucket)
        policy = json.loads(resp["Policy"])
        print(json.dumps(policy, indent=2)[:600])

    step("1. ListBuckets", list_buckets)
    step("2. ListObjects", list_objects)
    step("3. PutObject", put_object)
    step("4. GetObject", get_object)
    step("5. DeleteObject", delete_object)
    step("6. GetBucketPolicy", get_policy)

    print()
    print("7. Public URL (anonymous GET):")
    public_status = "FAIL"
    try:
        req = urllib.request.Request(f"{public_base}/{test_key}", method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"   OK — HTTP {r.status}, {len(r.read())} bytes")
            public_status = "OK"
    except urllib.error.HTTPError as e:
        if e.code == 404 and results.get("5. DeleteObject", ("FAIL",))[0] == "OK":
            print(f"   OK — HTTP 404: публичный read разрешён, объект удалён на шаге 5")
            public_status = "OK"
        else:
            print(f"   FAIL — HTTP {e.code}")
    except Exception as e:
        print(f"   FAIL — {e}")

    print()
    print("=== Summary ===")
    core_ops = ["2. ListObjects", "3. PutObject", "4. GetObject", "5. DeleteObject"]
    ok_all = all(results.get(op, ("FAIL",))[0] == "OK" for op in core_ops)

    if ok_all:
        print("Права на бакет есть: List/Put/Get/Delete работают.")
    else:
        print("Доступа к операциям с объектами НЕТ.")
        for op in core_ops:
            status, detail = results.get(op, ("SKIP", "not run"))
            if status == "FAIL":
                print(f"   {op}: {detail}")

    lb = results.get("1. ListBuckets", ("FAIL", ""))
    if lb[0] == "FAIL" and "AccessDenied" in str(lb[1]):
        print("ListBuckets AccessDenied — нормально для SA с правами только на один бакет.")

    if not ok_all:
        print()
        print("=== Terraform suggestions (НЕ применяются автоматически) ===")
        sa_ref = "cloudru_evolution_iam_service_account.aichat.id"
        print(f"""Проверь в main.tf политику бакета:
1. Principal должен ссылаться на SA этого проекта:
   AWS = "arn:aws:iam::${{var.object_storage_tenant_id}}:service_account/${{{sa_ref}}}"
2. Resource должен покрывать и бакет, и объекты:
   "arn:aws:s3:::{bucket}", "arn:aws:s3:::{bucket}/*"
3. SA нужна роль на бакет:
   resource "cloudru_evolution_iam_permission" "{bucket}_s3" {{
     resource_id    = cloudru_evolution_obs_bucket.{bucket}.uuid
     subject_id     = {sa_ref}
     role_identifier = {{ role_name = "s3e.admin" }}
   }}
4. Если ключ пересоздавался — обнови S3_SA_KEY_ID/S3_SA_KEY_SECRET в .env из
   terraform output s3_access_key_id / s3_access_key_secret
   (ключ используется в формате S3_TENANT_ID:S3_SA_KEY_ID)
5. После apply права реплицируются на ноды OBS несколько минут —
   если AccessDenied нестабилен (то OK, то FAIL), просто подожди и повтори.""")

    print()
    print("=== Done ===")
    return 0 if ok_all else 2


if __name__ == "__main__":
    raise SystemExit(main())
