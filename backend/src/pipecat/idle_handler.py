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
                "role": "developer",
                "content": "The user has been quiet for a moment. In one short, polite sentence, check if they are still with you (e.g. 'Are you still with me?'). Keep it under 10 words.",
            }
            await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))

        elif self._retry_count == 2:
            message = {
                "role": "developer",
                "content": "The user is still quiet. In one short, friendly sentence, ask if they would like to continue or if they need a moment. Keep it under 12 words.",
            }
            await aggregator.push_frame(LLMMessagesAppendFrame([message], run_llm=True))

        else:
            # third attempt so end the call gracefully
            message = {
                "role": "developer",
                "content": "The caller has remained silent. Deliver a single, brief farewell thanking them for calling and saying goodbye (e.g. 'Since I haven't heard from you, I'll go ahead and end the call. Thanks for calling, have a wonderful day'). Keep it under 20 words.",
            }
            if self._call_session is not None:
                self._call_session.end_reason = "idle_timeout"

            await aggregator.push_frame(
                LLMMessagesAppendFrame(messages=[message], run_llm=True)
            )

            await aggregator.push_frame(EndWorkerFrame())