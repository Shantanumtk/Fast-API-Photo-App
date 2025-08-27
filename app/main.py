import io, os, re, uuid
import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ----- Settings (env) -----
BUCKET_NAME       = os.getenv("BUCKET_NAME", "fastapi-photo-app-nik")
AWS_REGION        = os.getenv("AWS_REGION",  "us-east-1")
MAX_UPLOAD_MB     = int(os.getenv("MAX_UPLOAD_MB", "10"))  # limit uploads (MB)
ALLOWED_EXTS      = {"jpg", "jpeg", "png", "gif", "webp"}

# S3 client (uses EC2 instance role, no keys on disk)
s3 = boto3.client("s3", region_name=AWS_REGION)

# ----- App -----
app = FastAPI(title="FastAPI S3 Photo App")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

def sanitize_filename(name: str) -> str:
    # keep alphanum, dot, dash, underscore
    base = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return base[:120] or "file"

def ext_ok(name: str) -> bool:
    ext = (name.rsplit(".", 1)[-1]).lower() if "." in name else ""
    return ext in ALLOWED_EXTS

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    # list images via presigned URLs
    image_urls = []
    try:
        resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="images/")
        for obj in resp.get("Contents", []):
            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": BUCKET_NAME, "Key": obj["Key"]},
                ExpiresIn=3600
            )
            image_urls.append(url)
    except ClientError as e:
        return HTMLResponse(
            f"<h3>S3 Error: {e.response['Error'].get('Message','unknown')}</h3>", status_code=500
        )

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "image_urls": image_urls, "bucket": BUCKET_NAME, "max_mb": MAX_UPLOAD_MB}
    )

@app.post("/uploadform")
async def upload_form(file: UploadFile = File(...)):
    if not file.filename or not ext_ok(file.filename):
        raise HTTPException(status_code=400, detail="Only images: " + ", ".join(sorted(ALLOWED_EXTS)))

    # Read and check size
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (>{MAX_UPLOAD_MB} MB)")

    safe_name = sanitize_filename(file.filename)
    key = f"images/{uuid.uuid4()}-{safe_name}"

    try:
        s3.upload_fileobj(
            io.BytesIO(data),
            BUCKET_NAME,
            key,
            ExtraArgs={"ContentType": file.content_type or "application/octet-stream"}
        )
    except ClientError as e:
        raise HTTPException(status_code=500, detail=f"S3 upload failed: {e}")

    return RedirectResponse("/", status_code=303)

@app.post("/upload")
async def upload_api(file: UploadFile = File(...)):
    # same validations but returns JSON
    if not file.filename or not ext_ok(file.filename):
        raise HTTPException(status_code=400, detail="Only images: " + ", ".join(sorted(ALLOWED_EXTS)))
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (>{MAX_UPLOAD_MB} MB)")
    safe_name = sanitize_filename(file.filename)
    key = f"images/{uuid.uuid4()}-{safe_name}"
    try:
        s3.upload_fileobj(io.BytesIO(data), BUCKET_NAME, key,
                          ExtraArgs={"ContentType": file.content_type or "application/octet-stream"})
        url = s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET_NAME, "Key": key}, ExpiresIn=3600)
        return {"status": "ok", "key": key, "url": url}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=f"S3 upload failed: {e}")

@app.get("/list")
def list_images():
    # return JSON list of URLs
    resp = s3.list_objects_v2(Bucket=BUCKET_NAME, Prefix="images/")
    urls = []
    for obj in resp.get("Contents", []):
        urls.append(s3.generate_presigned_url(
            "get_object", Params={"Bucket": BUCKET_NAME, "Key": obj["Key"]}, ExpiresIn=3600
        ))
    return {"images": urls}

