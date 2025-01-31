from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse
import httpx
import urllib.parse
import re
import logging
import traceback
import sys

# Detaylı loglama ayarları
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = FastAPI()

# CORS ayarları
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
    "*"
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
        logger.warning("Empty content received")
        return False
    
    has_extinf = '#EXTINF' in content
    has_extm3u = '#EXTM3U' in content
    has_urls = bool(re.search(r'^https?://', content, re.MULTILINE))
    
    logger.debug(f"Content validation: EXTINF={has_extinf}, EXTM3U={has_extm3u}, URLs={has_urls}")
    
    return has_extinf or has_extm3u or has_urls

@app.get("/", response_class=PlainTextResponse)
async def health_check():
    return "OK"

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_details = {
        'error': str(exc),
        'type': type(exc).__name__,
        'traceback': traceback.format_exc()
    }
    logger.error(f"Global error: {error_details}")
    return JSONResponse(
        status_code=500,
        content=error_details
    )

@app.get("/proxy")
async def proxy(url: str, request: Request):
    if not url:
        logger.error("Missing URL parameter")
        raise HTTPException(status_code=400, detail="URL parameter is required")
    
    logger.info(f"Incoming request from: {request.client.host}")
    logger.info(f"Fetching URL: {url}")
    
    try:
        decoded_url = urllib.parse.unquote(url)
        logger.debug(f"Decoded URL: {decoded_url}")
        
        timeout = httpx.Timeout(30.0, connect=10.0)
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        
        async with httpx.AsyncClient(
            verify=False,
            timeout=timeout,
            limits=limits,
            follow_redirects=True
        ) as client:
            headers = {
                'User-Agent': 'VLC/3.0.16 LibVLC/3.0.16',
                'Accept': '*/*',
                'Accept-Language': 'tr-TR,tr;q=0.9',
                'Connection': 'keep-alive'
            }
            
            logger.info("Sending request to target server...")
            try:
                response = await client.get(decoded_url, headers=headers)
                logger.info(f"Target server response status: {response.status_code}")
                logger.debug(f"Response headers: {dict(response.headers)}")
            except httpx.RequestError as e:
                logger.error(f"Request failed: {str(e)}")
                return JSONResponse(
                    status_code=500,
                    content={"error": f"Request failed: {str(e)}"}
                )
            
            if response.status_code != 200:
                logger.error(f"Target server error: {response.status_code}")
                return JSONResponse(
                    status_code=response.status_code,
                    content={"error": f"Target server returned {response.status_code}"}
                )
            
            try:
                content = response.text
                logger.info(f"Received content length: {len(content)}")
                if len(content) > 0:
                    logger.debug(f"Content preview: {content[:200]}")
            except Exception as e:
                logger.error(f"Failed to decode content: {str(e)}")
                return JSONResponse(
                    status_code=500,
                    content={"error": f"Failed to decode content: {str(e)}"}
                )
            
            if not await is_m3u_content(content):
                logger.error("Invalid M3U content")
                return JSONResponse(
                    status_code=400,
                    content={"error": "Invalid M3U format"}
                )
            
            response_headers = {
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
                'Access-Control-Allow-Headers': '*',
                'Cache-Control': 'public, max-age=300',
                'Content-Type': 'text/plain; charset=utf-8'
            }
            
            logger.info("Sending successful response")
            return PlainTextResponse(
                content=content,
                headers=response_headers
            )
            
    except httpx.RequestError as e:
        error_msg = f"Request error: {str(e)}"
        logger.error(error_msg)
        return JSONResponse(
            status_code=500,
            content={"error": error_msg}
        )
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"error": error_msg}
        )
