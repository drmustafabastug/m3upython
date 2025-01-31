from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import urllib.parse
import re
import logging
import traceback
import sys
from typing import List, Dict, Optional, Tuple
import json
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential
from datetime import datetime, timedelta
from cachetools import TTLCache
import hashlib

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = FastAPI()

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Initialize cache with 1 hour TTL
channel_cache = TTLCache(maxsize=100, ttl=3600)

class Channel:
    def __init__(self):
        self.title: str = ""
        self.logo: Optional[str] = None
        self.group: Optional[str] = ""
        self.url: str = ""
        self.id: Optional[str] = None
        self.language: Optional[str] = None
        self.country: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "logo": self.logo,
            "group": self.group,
            "url": self.url,
            "id": self.id,
            "language": self.language,
            "country": self.country
        }

def clean_attribute(attr: str) -> str:
    """Clean and normalize M3U attributes."""
    return attr.strip().strip('"\'')

async def parse_m3u(content: str) -> List[Channel]:
    channels = []
    current_channel = None
    
    try:
        for line in content.splitlines():
            line = line.strip()
            
            if not line:
                continue
                
            if line.startswith('#EXTINF:'):
                current_channel = Channel()
                
                # Parse all available attributes
                attributes = {
                    'tvg-name': 'title',
                    'tvg-logo': 'logo',
                    'group-title': 'group',
                    'tvg-id': 'id',
                    'tvg-language': 'language',
                    'tvg-country': 'country'
                }
                
                for attr, field in attributes.items():
                    match = re.search(f'{attr}="([^"]*)"', line)
                    if match:
                        setattr(current_channel, field, clean_attribute(match.group(1)))
                
                # If no tvg-name, try to get title from the end of the line
                if not current_channel.title:
                    title = line.split(',')[-1].strip()
                    current_channel.title = clean_attribute(title)
                    
            elif line.startswith('http://') or line.startswith('https://'):
                if current_channel:
                    current_channel.url = line
                    channels.append(current_channel)
                    current_channel = None
            elif line.startswith('#EXTM3U'):
                continue
            else:
                logger.debug(f"Skipping unrecognized line: {line}")
                
    except Exception as e:
        logger.error(f"Error parsing M3U content: {str(e)}")
        logger.error(f"Problematic line: {line}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse M3U content: {str(e)}"
        )
    
    return channels

@app.get("/")
async def health_check():
    return {"status": "OK", "timestamp": datetime.utcnow().isoformat()}

def get_cache_key(url: str) -> str:
    """Generate a cache key from URL."""
    return hashlib.md5(url.encode()).hexdigest()

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def fetch_url(url: str) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
    }
    
    async with httpx.AsyncClient(
        verify=False,
        timeout=httpx.Timeout(60.0, connect=20.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        http2=True
    ) as client:
        try:
            response = await client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('content-type', '')
            if not any(t in content_type.lower() for t in ['text/plain', 'application/x-mpegurl', 'application/vnd.apple.mpegurl']):
                logger.warning(f"Unexpected content type: {content_type}")
            
            return response.text
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"HTTP error: {e.response.status_code} - {e.response.text}"
            )
        except httpx.RequestError as e:
            logger.error(f"Request error occurred: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Request failed: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Unexpected error during fetch: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Internal server error: {str(e)}"
            )

@app.get("/channels")
async def get_channels(url: str, request: Request, force_refresh: bool = False):
    """
    Fetch and parse M3U channels from the given URL.
    
    Args:
        url: The URL to fetch M3U content from
        request: The FastAPI request object
        force_refresh: If True, bypass cache and fetch fresh data
    """
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")
    
    if not url.startswith(('http://', 'https://')):
        raise HTTPException(status_code=400, detail="Invalid URL format")
    
    logger.info(f"Incoming request from: {request.client.host}")
    logger.debug(f"Fetching URL: {url}")
    
    try:
        decoded_url = urllib.parse.unquote(url)
        cache_key = get_cache_key(decoded_url)
        
        # Check cache first
        if not force_refresh and cache_key in channel_cache:
            logger.info("Returning cached response")
            return channel_cache[cache_key]
        
        logger.info("Sending request to target server...")
        content = await fetch_url(decoded_url)
        
        if not content:
            raise HTTPException(status_code=500, detail="Received empty content from server")
        
        logger.info(f"Received content length: {len(content)}")
        if len(content) > 0:
            logger.debug(f"Content preview: {content[:200]}")
        
        channels = await parse_m3u(content)
        logger.info(f"Successfully parsed {len(channels)} channels")
        
        result = {
            "total": len(channels),
            "channels": [channel.to_dict() for channel in channels],
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Cache the result
        channel_cache[cache_key] = result
        
        return JSONResponse(content=result)
                
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
