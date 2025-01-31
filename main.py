from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
import httpx
import urllib.parse
import re
import logging

# Logging ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS ayarları - daha geniş
origins = [
    "http://localhost",
    "http://localhost:8000",
    "http://localhost:19000",
    "http://localhost:19001",
    "http://localhost:19002",
    "http://localhost:19006",
    "exp://localhost:19000",
    "exp://localhost:19001",
    "exp://localhost:19002",
    "exp://localhost:19006",
    "*"  # Geliştirme aşamasında tüm originlere izin ver
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

async def is_m3u_content(content: str) -> bool:
    if not content:
        return False
    
    return (
        '#EXTINF' in content or 
        '#EXTM3U' in content or 
        bool(re.search(r'^https?://', content, re.MULTILINE))
    )

@app.get("/", response_class=PlainTextResponse)
async def health_check():
    return "OK"

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"error": str(exc)}
    )

@app.get("/proxy")
async def proxy(url: str, request: Request):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")
    
    logger.info(f"Incoming request from: {request.client.host}")
    logger.info(f"Fetching URL: {url}")
    
    try:
        # URL decode
        decoded_url = urllib.parse.unquote(url)
        
        # httpx client with SSL verification disabled and timeout
        async with httpx.AsyncClient(
            verify=False, 
            timeout=30.0,
            follow_redirects=True
        ) as client:
            headers = {
                'User-Agent': 'VLC/3.0.16 LibVLC/3.0.16',
                'Accept': '*/*',
                'Accept-Language': 'tr-TR,tr;q=0.9',
                'Connection': 'keep-alive'
            }
            
            logger.info("Sending request to target server...")
            response = await client.get(decoded_url, headers=headers)
            logger.info(f"Target server response status: {response.status_code}")
            
            # Status code kontrolü
            if response.status_code != 200:
                logger.error(f"Target server error: {response.status_code}")
                return JSONResponse(
                    status_code=response.status_code,
                    content={"error": f"Target server returned {response.status_code}"}
                )
            
            content = response.text
            logger.info(f"Received content length: {len(content)}")
            
            # M3U içerik kontrolü
            if not await is_m3u_content(content):
                logger.error("Invalid M3U content")
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid M3U format"}
                )
            
            # Response headers
            response_headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': '*',
                'Cache-Control': 'public, max-age=300',
                'Content-Type': 'text/plain; charset=utf-8'
            }
            
            return PlainTextResponse(
                content=content,
                headers=response_headers
            )
            
    except httpx.RequestError as e:
        logger.error(f"Request error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Request error: {str(e)}"}
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
