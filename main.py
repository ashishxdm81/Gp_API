from fastapi import FastAPI, HTTPException
import httpx
import re
import demjson3
import logging
from contextlib import asynccontextmanager
from typing import Optional, Dict, List

# Configure lightweight logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

app = FastAPI()

# Shared HTTP client for connection pooling
client = httpx.AsyncClient(
    headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
    },
    timeout=15.0
)

@asynccontextmanager
async def managed_client():
    """Manage HTTP client lifecycle to prevent resource leaks."""
    try:
        yield client
    finally:
        pass  # Client is reused, closed only on shutdown

async def get_google_photos_links(url: str) -> Optional[Dict]:
    """
    Lightweight function to fetch and parse Google Photos page for direct CDN URLs.
    Uses async HTTP requests and minimal memory footprint.
    """
    async with managed_client() as http_client:
        try:
            response = await http_client.get(url)
            response.raise_for_status()
        except httpx.RequestError as e:
            logger.error(f"Fetch error: {e}")
            raise HTTPException(status_code=500, detail=f"Could not fetch URL: {str(e)}")

        # Process only necessary portion of HTML
        match = re.search(r'AF_initDataCallback\((.*?)\);</script>', response.text, re.DOTALL)
        if not match:
            logger.error("AF_initDataCallback not found")
            raise HTTPException(status_code=500, detail="Could not find data block")

        try:
            data = demjson3.decode(match.group(1), strict=False)
            video_info_block = data.get('data', [])[0]
            if not isinstance(video_info_block, list) or len(video_info_block) < 2:
                logger.error("Invalid video info block")
                raise HTTPException(status_code=500, detail="Invalid video info format")

            base_url = video_info_block[1][0]
            file_code = video_info_block[0]
            download_url = data.get('data', [])[1]

            stream_data_block = None
            for item in video_info_block:
                if isinstance(item, dict):
                    for value in item.values():
                        if isinstance(value, list) and len(value) > 7 and isinstance(value[7], list):
                            stream_data_block = value
                            break
                if stream_data_block:
                    break

            if not stream_data_block:
                logger.error("Stream data block not found")
                raise HTTPException(status_code=500, detail="Stream data block not found")

            quality_info = stream_data_block[7]

            # Build response with minimal memory usage
            quality_map = {'37': '1080p', '22': '720p', '18': '360p', '36': '180p'}
            stream_urls = [
                {
                    'label': quality_map.get(str(quality[0]), f"Resolution {quality[1]}x{quality[2]}"),
                    'url': f"{base_url}=m{quality[0]}"
                }
                for quality in quality_info
            ]

            return {
                'status': True,
                'host': 'googlephoto',
                'filecode': file_code,
                'poster': f"{base_url}=w1920-h1080-no",
                'streams': stream_urls,
                'download': download_url,
                'vtt': None
            }

        except (KeyError, IndexError, TypeError, demjson3.JSONDecodeError) as e:
            logger.error(f"Parse error: {e}")
            raise HTTPException(status_code=500, detail=f"Could not parse data: {str(e)}")

@app.get("/extract")
async def extract_links(URL: str):
    """
    Endpoint to extract Google Photos links.
    Accepts unanimated photos.google.com and photos.app.goo.gl URLs.
    """
    if not URL or ("photos.app.goo.gl" not in URL and "photos.google.com" not in URL):
        raise HTTPException(status_code=400, detail="Invalid or missing Google Photos URL")

    result = await get_google_photos_links(URL)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to extract links")

    return result

@app.get("/")
async def root():
    return {"message": "Google Photos Link Extractor API"}

@app.on_event("shutdown")
async def shutdown_event():
    """Close HTTP client on shutdown to free resources."""
    await client.aclose()
