from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import urllib.parse
import re
import logging
import traceback
import sys
from typing import List, Dict, Optional
import json
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

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

class Channel:
    def __init__(self):
        self.title: str = ""
        self.logo: Optional[str] = None
        self.group: Optional[str] = ""
        self.url: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "title": self.title,
            "logo": self.logo,
            "group": self.group,
            "url": self.url
        }

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
                
                # Parse title
                title_match = re.search(r'tvg-name="([^"]*)"', line)
                if title_match:
                    current_channel.title = title_match.group(1)
                else:
                    # If no tvg-name, try to get title from the end of the line
                    title = line.split(',')[-1].strip()
                    current_channel.title = title
                
                # Parse logo
                logo_match = re.search(r'tvg-logo="([^"]*)"', line)
                if logo_match:
                    current_channel.logo = logo_match.group(1)
                
                # Parse group
                group_match = re.search(r'group-title="([^"]*)"', line)
                if group_match:
                    current_channel.group = group_match.group(1)
                    
            elif line.startswith('http://') or line.startswith('https://'):
                if current_channel:
                    current_channel.url = line
                    channels.append(current_channel)
                    current_channel = None
    except Exception as e:
        logger.error(f"Error parsing M3U content: {str(e)}")
        logger.error(f"Problematic line: {line}")
        raise
    
    return channels

@app.get("/")
async def health_check():
    return {"status": "OK"}

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def fetch_url(url: str, headers: Dict) -> str:
    async with httpx.AsyncClient(
        verify=False,
        timeout=httpx.Timeout(60.0, connect=20.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    ) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            return response.text
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error occurred: {e.response.status_code} - {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except httpx.RequestError as e:
            logger.error(f"Request error occurred: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"Unexpected error during fetch: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/channels")
async def get_channels(url: str, request: Request):
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")
    
    logger.info(f"Incoming request from: {request.client.host}")
    logger.info(f"Fetching URL: {url}")
    
    try:
        decoded_url = urllib.parse.unquote(url)
        logger.debug(f"Decoded URL: {decoded_url}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }
        
        logger.info("Sending request to target server...")
        content = await fetch_url(decoded_url, headers)
        
        logger.info(f"Received content length: {len(content)}")
        if len(content) > 0:
            logger.debug(f"Content preview: {content[:200]}")
        
        channels = await parse_m3u(content)
        logger.info(f"Successfully parsed {len(channels)} channels")
        
        result = {
            "total": len(channels),
            "channels": [channel.to_dict() for channel in channels]
        }
        
        return JSONResponse(content=result)
                
    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)
