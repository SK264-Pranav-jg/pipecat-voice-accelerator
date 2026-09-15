""" complete idle handler implementation for handling idle periods in 
calls to avoid long silences in calls """ 
from pipecat.frames.frames import (
    LLMMessagesAppendFrame , 
    TTSSpeakFrame , 
    EndWorkerFrame, 
)
from pipecat.pipeline.pipeline import FrameDirection



class IdleHandler: 
    def __init__(self): 
        self._retry_count = 0 

    async def reset(self): 
        self._retry_count = 0 

    async def handle_idle(self , aggregator): 
        self._retry_count += 1

        if self._retry_count == 1: 
            message = { 
                "role" : "developer" , 
                "content" : "The user has been quiet , Politely and briefly ask if they are still there", 
            } 
            await aggregator.push_frame(LLMMessagesAppendFrame([message],run_llm=True))
        
        elif self._retry_count == 2:
            message = {
                "role" : "developer" , 
                "content" : " The user is still inactive , ASk if they'd like to continue our conversation"
            }
            await aggregator.push_frame(LLMMessagesAppendFrame([message],run_llm=True))
        
        else: 
            # third attempt so end the call gracefully 
            message = {
                "role" : "developer" , 
                "content" : "Seems like the caller is not responding and could be busy , thank them and say a finishing statement gracefully"
            }
            await aggregator.push_frame(
                LLMMessagesAppendFrame(messages=[message],run_llm=True)
            )

            await aggregator.push_frame(EndWorkerFrame())