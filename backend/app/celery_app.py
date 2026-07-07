import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

RAW_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Uniformly append query string parameters if it's a secure connection
if RAW_REDIS_URL.startswith("rediss://") and "ssl_cert_reqs" not in RAW_REDIS_URL:
    separator = "&" if "?" in RAW_REDIS_URL else "?"
    FINAL_REDIS_URL = f"{RAW_REDIS_URL}{separator}ssl_cert_reqs=CERT_NONE"
else:
    FINAL_REDIS_URL = RAW_REDIS_URL

celery_app = Celery(
    "audito_worker",
    broker=FINAL_REDIS_URL,   # Clean string parameters for Kombu task dispatch
    backend=FINAL_REDIS_URL,  # Clean string parameters for Celery result backend
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
    task_track_started=True,
    
    broker_transport_options={
        "visibility_timeout": 3600,
    },
)