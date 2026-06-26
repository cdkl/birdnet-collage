import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BIRDNET_GO_URL = os.getenv("BIRDNET_GO_URL", "http://birdnet-go.local:8080")
    BIRDNET_GO_URL = BIRDNET_GO_URL.rstrip("/")
    BIRDNET_GO_TOKEN = os.getenv("BIRDNET_GO_TOKEN", "")
SITE_TITLE = os.getenv("SITE_TITLE", "birdnet collage")
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", "8081"))
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"