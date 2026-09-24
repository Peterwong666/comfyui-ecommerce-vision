#!/usr/bin/env python3
"""Minimal S3-compatible mock server for testing (replaces MinIO).

Handles: PUT/GET/HEAD object, ListBuckets, CreateBucket,
         multipart upload (Initiate/UploadPart/Complete).
"""
import os
import sys
import uuid
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from xml.etree import ElementTree as ET

DATA_DIR = "/root/minio-data"
UPLOADS_DIR = "/root/minio-data/.multipart"

class S3Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _respond(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _parse_path(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        parts = parsed.path.strip("/").split("/", 1)
        return parsed, qs, parts

    def do_GET(self):
        parsed, qs, parts = self._parse_path()

        if path := parsed.path:
            if path == "/" or path == "":
                buckets = [d for d in os.listdir(DATA_DIR)
                           if os.path.isdir(os.path.join(DATA_DIR, d)) and not d.startswith(".")]
                xml = '<?xml version="1.0"?><ListAllMyBucketsResult><Buckets>'
                for b in buckets:
                    xml += f'<Bucket><Name>{b}</Name></Bucket>'
                xml += '</Buckets></ListAllMyBucketsResult>'
                self._respond(200, xml.encode(), {"Content-Type": "application/xml"})
                return

        if len(parts) == 1:
            bucket = parts[0]
            bucket_dir = os.path.join(DATA_DIR, bucket)
            os.makedirs(bucket_dir, exist_ok=True)
            prefix = qs.get("prefix", [""])[0]
            files = [f for f in os.listdir(bucket_dir) if f.startswith(prefix) and not f.startswith(".")]
            xml = '<?xml version="1.0"?><ListBucketResult>'
            for f in files:
                fp = os.path.join(bucket_dir, f)
                xml += f'<Contents><Key>{f}</Key><Size>{os.path.getsize(fp)}</Size></Contents>'
            xml += '</ListBucketResult>'
            self._respond(200, xml.encode(), {"Content-Type": "application/xml"})
            return

        bucket, key = parts
        filepath = os.path.join(DATA_DIR, bucket, key)
        if os.path.exists(filepath):
            with open(filepath, "rb") as f:
                data = f.read()
            self._respond(200, data, {"Content-Length": str(len(data))})
        else:
            self._respond(404, b"<Error><Code>NoSuchKey</Code></Error>")

    def do_HEAD(self):
        _, _, parts = self._parse_path()
        if len(parts) == 2:
            bucket, key = parts
            filepath = os.path.join(DATA_DIR, bucket, key)
            if os.path.exists(filepath):
                self._respond(200, headers={"Content-Length": str(os.path.getsize(filepath))})
                return
        self._respond(404)

    def do_PUT(self):
        _, qs, parts = self._parse_path()
        if len(parts) < 2:
            os.makedirs(os.path.join(DATA_DIR, parts[0]), exist_ok=True)
            self._respond(200)
            return

        bucket, key = parts
        filepath = os.path.join(DATA_DIR, bucket, key)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        # Multipart upload part
        upload_id = qs.get("uploadId", [None])[0]
        part_number = qs.get("partNumber", [None])[0]
        if upload_id and part_number:
            part_dir = os.path.join(UPLOADS_DIR, upload_id)
            os.makedirs(part_dir, exist_ok=True)
            content_length = int(self.headers.get("Content-Length", 0))
            data = self.rfile.read(content_length)
            with open(os.path.join(part_dir, f"{int(part_number):05d}"), "wb") as f:
                f.write(data)
            etag = hashlib.md5(data).hexdigest()
            self._respond(200, headers={"ETag": f'"{etag}"'})
            return

        # Simple PUT
        content_length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(content_length)
        with open(filepath, "wb") as f:
            f.write(data)
        self._respond(200)

    def do_POST(self):
        parsed, qs, parts = self._parse_path()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Complete multipart upload
        upload_id = qs.get("uploadId", [None])[0]
        if upload_id and len(parts) >= 2:
            bucket, key = parts
            filepath = os.path.join(DATA_DIR, bucket, key)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            part_dir = os.path.join(UPLOADS_DIR, upload_id)
            if os.path.exists(part_dir):
                part_files = sorted(os.listdir(part_dir))
                with open(filepath, "wb") as out:
                    for pf in part_files:
                        with open(os.path.join(part_dir, pf), "rb") as inp:
                            out.write(inp.read())
                # Cleanup parts
                import shutil
                shutil.rmtree(part_dir, ignore_errors=True)

            etag = hashlib.md5(open(filepath, "rb").read()).hexdigest()
            xml = f'<?xml version="1.0"?><CompleteMultipartUploadResult><ETag>"{etag}"</ETag></CompleteMultipartUploadResult>'
            self._respond(200, xml.encode(), {"Content-Type": "application/xml"})
            return

        # Initiate multipart upload
        if len(parts) >= 2:
            upload_id = str(uuid.uuid4())
            os.makedirs(os.path.join(UPLOADS_DIR, upload_id), exist_ok=True)
            xml = f'<?xml version="1.0"?><InitiateMultipartUploadResult><UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>'
            self._respond(200, xml.encode(), {"Content-Type": "application/xml"})
            return

        self._respond(400, b"BadRequest")

    def do_DELETE(self):
        _, _, parts = self._parse_path()
        if len(parts) == 2:
            bucket, key = parts
            filepath = os.path.join(DATA_DIR, bucket, key)
            if os.path.exists(filepath):
                os.remove(filepath)
                self._respond(204)
                return
        self._respond(204)

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    server = HTTPServer(("0.0.0.0", port), S3Handler)
    print(f"Mock S3 server running on :{port} (with multipart upload support)")
    server.serve_forever()