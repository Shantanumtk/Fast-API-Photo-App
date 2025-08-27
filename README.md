# FastAPI S3 Photo App

A minimal image **upload + gallery** web app built with **FastAPI** on **EC2**, storing images in **S3**, running inside a **custom VPC**. Frontend is a simple HTML form + gallery (Jinja2). Backend exposes JSON APIs with presigned S3 URLs. Uses instance IAM role (no keys on disk).

---

## 1) Step‑by‑step: Create AWS Infrastructure

> Region examples use `us-east-1`. Replace names as needed.

### 1.1 Create an S3 bucket (private)

* Console → **S3 → Create bucket**
* **Name:** `fastapi-photo-app-<yourname>` (globally unique)
* **Block public access:** ON (default)
* Create

*(Optional)* Create prefix `images/` (not required — the app uses it automatically).

### 1.2 Create least‑privilege IAM policy + role for EC2

**Policy JSON** (replace bucket name everywhere):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::fastapi-photo-app-yourname"],
      "Condition": {"StringLike": {"s3:prefix": ["images/*"]}}
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject","s3:PutObject","s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::fastapi-photo-app-yourname/images/*"]
    }
  ]
}
```

* Console → **IAM → Policies → Create policy → JSON** → paste → save as `FastApiS3LeastPrivilege`.
* Console → **IAM → Roles → Create role** → **Trusted entity:** EC2 → attach policy `FastApiS3LeastPrivilege` → name `EC2-FastApiS3Role` → Create.

### 1.3 Build a custom VPC (public subnet)

1. **VPC**: VPC Dashboard → Create VPC → *VPC only*

   * Name: `fastapi-photo-vpc`
   * CIDR: `10.0.0.0/16`

2. **Subnet**: Subnets → Create

   * VPC: `fastapi-photo-vpc`
   * AZ: choose one (e.g., `us-east-1a`)
   * CIDR: `10.0.1.0/24`
   * Name: `public-subnet-1`
   * After creation: **Actions → Edit subnet settings → Enable auto‑assign public IPv4**

3. **Internet Gateway**: Internet Gateways → Create → `fastapi-photo-igw` → Attach to `fastapi-photo-vpc`.

4. **Route Table**: Route tables → Create → `public-rt` (VPC: `fastapi-photo-vpc`).

   * **Routes → Edit** → add `0.0.0.0/0 → Internet Gateway (fastapi-photo-igw)`
   * **Subnet associations → Edit** → associate `public-subnet-1`.

### 1.4 Security Group

* EC2 → Security Groups → Create → `fastapi-photo-sg` (VPC: `fastapi-photo-vpc`)
* **Inbound rules**:

  * HTTP **80** → `0.0.0.0/0`
  * (While testing) Custom TCP **8000** → your IP or `0.0.0.0/0`
  * SSH **22** → *Your IP only*
* Outbound: allow all

### 1.5 Launch EC2 in the custom VPC

* EC2 → Launch instance → Name `fastapi-photo-ec2`
* AMI: **Ubuntu 22.04** (or Amazon Linux 2023)
* Type: `t2.micro`
* Key pair: select your `.pem`
* Network: `fastapi-photo-vpc` | Subnet: `public-subnet-1`
* Auto-assign public IP: **Enable**
* **IAM role:** `EC2-FastApiS3Role`
* **Security group:** `fastapi-photo-sg`
* Launch → note **Public IPv4 address**

### 1.6 Provision the instance (packages, app, Python venv)

SSH in (Ubuntu default user):

```bash
ssh -i my-key.pem ubuntu@<EC2_PUBLIC_IP>
```

Install basics:

```bash
sudo apt update -y
sudo apt install -y python3-venv git unzip nginx
```

### 1.7 Create project + virtualenv

```bash
mkdir -p ~/fastapi-photo/app/templates ~/fastapi-photo/app/static
cd ~/fastapi-photo
python3 -m venv .venv
source .venv/bin/activate
cat > requirements.txt << 'EOF'
fastapi
uvicorn[standard]
boto3
python-multipart
jinja2
python-dotenv
EOF
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 1.8 Environment variables (.env)

Create project‑local `.env` (loaded by `python-dotenv`):

```bash
cat > .env << 'EOF'
BUCKET_NAME=fastapi-photo-app-yourname
AWS_REGION=us-east-1
MAX_UPLOAD_MB=10
EOF
```

### 1.9 Run the app (dev) on port 8000

```bash
cd ~/fastapi-photo
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Security Group must allow inbound TCP **8000** (or use Nginx below for port **80**).

### 1.10 Nginx reverse proxy (80 → 8000)

Bind app to localhost (safer) via systemd and proxy via Nginx.

**systemd service**:

```bash
sudo tee /etc/systemd/system/fastapi-photo.service > /dev/null <<'UNIT'
[Unit]
Description=FastAPI S3 Photo App
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/fastapi-photo
Environment="PYTHONPATH=/home/ubuntu/fastapi-photo"
ExecStart=/home/ubuntu/fastapi-photo/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now fastapi-photo
sudo systemctl status fastapi-photo --no-pager
```

**Nginx site**:

```bash
sudo tee /etc/nginx/sites-available/fastapi-photo > /dev/null <<'NGINX'
server {
    listen 80;
    server_name _;
    client_max_body_size 25M;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout  120s;
        proxy_send_timeout  120s;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/fastapi-photo /etc/nginx/sites-enabled/fastapi-photo
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Browse: `http://<EC2_PUBLIC_IP>/`

---

## 2) Directory structure

```
fastapi-photo/
├── .env
├── requirements.txt
├── .venv/                      # Python virtualenv (created on the server)
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app (routes, S3, templates)
│   ├── templates/
│   │   └── index.html          # Upload form + gallery
│   └── static/
│       └── styles.css          # Simple CSS
└── (system)
    ├── /etc/systemd/system/fastapi-photo.service
    └── /etc/nginx/sites-available/fastapi-photo → sites-enabled/
```

---

## 3) Code usage (backend + frontend)

### Backend (FastAPI endpoints)

* `GET /healthz` → `{ "ok": true }`
* `GET /` → HTML page with upload form + gallery
* `POST /uploadform` → form upload; redirects to `/`
* `POST /upload` → JSON API; multipart form field **file**; returns `{status,key,url}`
* `GET /list` → JSON list of presigned image URLs

**Environment variables** (via `.env`):

* `BUCKET_NAME` — S3 bucket name (private)
* `AWS_REGION` — bucket region (e.g., `us-east-1`)
* `MAX_UPLOAD_MB` — upload size limit (default 10)

**Notes**

* Uploads stored under `images/` prefix.
* Presigned URLs expire in 1 hour.
* Allowed extensions: `jpg, jpeg, png, gif, webp`.
* Returns **400** for bad extension, **413** if file too large.

### Frontend (HTML + Jinja2)

* Visit `http://<EC2_PUBLIC_IP>/` (or `:8000/` without Nginx)
* Use the file picker to upload an image
* Page refresh shows a responsive grid of thumbnails (served via presigned URLs)

---

## 4) Testing all APIs

### 4.1 Quick health

```bash
curl -i http://<EC2_PUBLIC_IP>/healthz
```

### 4.2 Create a tiny test image

* **On macOS:**

```bash
echo iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII= | base64 -D > test.png
```

* **On Linux:**

```bash
echo iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII= | base64 -d > test.png
```

### 4.3 Upload via JSON API

```bash
curl -i -X POST \
  -F "file=@test.png" \
  http://<EC2_PUBLIC_IP>/upload
```

**Expect:** `{"status":"ok","key":"images/<uuid>-test.png","url":"https://..."}`

### 4.4 List images

```bash
curl -s http://<EC2_PUBLIC_IP>/list
```

**Expect:** `{"images": ["https://...", "https://..."]}`

### 4.5 Frontend test

Open in a browser: `http://<EC2_PUBLIC_IP>/` → upload a few images → thumbnails appear.

### 4.6 Negative tests

* **Disallowed extension**

```bash
echo "hello" > not-image.txt
curl -i -X POST -F "file=@not-image.txt" http://<EC2_PUBLIC_IP>/upload
```

Expect **400**.

* **Too large** (if `MAX_UPLOAD_MB=1`)

```bash
dd if=/dev/zero of=big.jpg bs=1M count=2
curl -i -X POST -F "file=@big.jpg" http://<EC2_PUBLIC_IP>/upload
```

Expect **413**.

---

## Troubleshooting quick reference

* **403 AccessDenied (S3)** → Verify IAM role attached to instance and policy bucket name matches. Check region.
* **Can’t reach frontend** → App running? `ss -tulpen | grep 8000` → Security Group allows port 80/8000 → Nginx `nginx -t`.
* **PEP 668 / pip blocked** → Use virtualenv: `python3 -m venv .venv && source .venv/bin/activate`.
* **Template errors** → Ensure `jinja2` installed and `app/templates/index.html` exists.
* **Import errors** → Ensure `app/__init__.py` exists; run `uvicorn app.main:app` from project root.
* **Logs**: `sudo journalctl -u fastapi-photo -f`, `sudo tail -f /var/log/nginx/error.log`.

---

## Cleanup (avoid charges)

* Terminate EC2 instance (or stop when not in use)
* Empty & delete S3 bucket
* Delete IAM role & policy (if not reused)
* Delete custom VPC resources: subnet, route table, IGW, VPC
