import os
import asyncio 
from contextlib import asynccontextmanager 
import logging 
import uuid 
import sys 
import uvicorn 

# fastapi imports 
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware 
from fastapi.responses import JSONResponse , Response , StreamingResponse 

# backend imports 
from backend.src.config.settings import settings 
from backend.src.pipecat.pipeline import build_pipeline

# logging 
logger = logging.getLogger(__name__) 

@asynccontextmanager
async def lifespan(app:FastAPI): 
    logger.info("Starting up...")
    yield
    logger.info("Shutting down...")

# app factory 
app = FastAPI(
    title="Pipecat Voice Accelerator",
    version="0.1.0",
    description="Real-time voice accelerator ",
    lifespan=lifespan
)

# CORS middleware 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/") 
async def root(): 
    return {"message" : "Pipecat Voice Accelerator - Health check"} 

@app.websocket("/ws",tags=["websocket"]) 
async def websocket_endpoint(
    websocket : WebSocket , 
) :
    "main websocket endpoint"

    await websocket.accept() 

# run the app 
if __name__ == "__main__":
    # run the app using uvicorn 
    uvicorn.run(
        "backend.src.api.app:app", 
        host=settings.host, 
        port=settings.port, 
        reload=True, 
        log_level="info" 
    ) 