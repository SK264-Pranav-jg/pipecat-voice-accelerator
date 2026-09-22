""" complete idle handler implementation for handling idle periods in 
calls to avoid long silences in calls """ 
from pipecat.frames.frames import (
    LLMMessagesAppendFrame , 
    TTSSpeakFrame , 
    EndWorkerFrame, 
)
from pipecat.pipeline.pipeline import FrameDirection



class IdleHandler:
    def __init__(self, call_session=None):
        self._retry_count = 0
        self._call_session = call_session

    # once the user starts speaking reset the counter
    async def reset(self): 
        self._retry_count = 0 

    # increment the counter for every idle detector trigger 
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
                "content" : "Acknowledge that this could be due to a faulty connection or the caller might be busy , Seems like the caller is not responding and could be busy , thank them and say a finishing statement gracefully"
            }
            if self._call_session is not None:
                self._call_session.end_reason = "idle_timeout"

            await aggregator.push_frame(
                LLMMessagesAppendFrame(messages=[message],run_llm=True)
            )

            await aggregator.push_frame(EndWorkerFrame())