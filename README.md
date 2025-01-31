# Python M3U Proxy

Simple and efficient M3U playlist proxy server built with FastAPI.

## Features

- CORS support
- SSL verification disabled
- Timeout handling
- M3U content validation
- Caching headers
- Error handling

## Installation

```bash
pip install -r requirements.txt
```

## Running the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Usage

Send GET request to `/proxy` endpoint with `url` parameter:

```
http://localhost:8000/proxy?url=YOUR_M3U_URL
```
